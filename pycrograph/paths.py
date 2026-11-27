# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""Where the workspace lives. Standard library only (no Qt): the Pyblication
plugin imports it from a headless process."""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_ENV = "PYCROGRAPH_WORKSPACE"


def default_workspace() -> Path:
    """~/.pycrograph, overridable with PYCROGRAPH_WORKSPACE (tests, demos,
    and the Pyblication plugin's open_project)."""
    env = os.environ.get(WORKSPACE_ENV)
    return Path(env).expanduser() if env else Path.home() / ".pycrograph"
