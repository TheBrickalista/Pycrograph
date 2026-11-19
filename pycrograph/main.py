# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

import sys


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
    run(argv)


if __name__ == "__main__":
    main()
