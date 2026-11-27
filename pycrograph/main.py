# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

import sys


def parse_project_arg(argv: list[str]) -> str | None:
    """The project id given with --project ID / --project=ID, or None.

    Used by the Pyblication plugin's open_project() to open a given project;
    without it the app starts exactly as before."""
    # SHARED-CORE candidate (from PysternBlot: pysternblot/main.py parse_project_arg)
    for i, arg in enumerate(argv):
        if arg == "--project" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--project="):
            return arg.split("=", 1)[1] or None
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selfcheck" in argv:
        # Offline check of an installed / packaged app (see selfcheck.py).
        out = None
        if "--selfcheck-out" in argv:
            i = argv.index("--selfcheck-out")
            out = argv[i + 1] if i + 1 < len(argv) else None
        from .selfcheck import run_selfcheck
        sys.exit(run_selfcheck(out))
    from .app import run
    project_id = parse_project_arg(argv)
    if project_id:
        run(argv, project_id=project_id)
    else:
        run(argv)


if __name__ == "__main__":
    main()
