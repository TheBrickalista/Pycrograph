# Installation

Pycrograph runs on macOS and Windows (and anywhere Python and PySide6 run).

| Method | Best for | Python needed? |
|---|---|---|
| **Standalone app** | Most users | No |
| **PyPI (`pip install`)** | Python users; Intel Macs | Yes (≥ 3.11) |
| **From source** | Contributors | Yes (≥ 3.11) |

## Option 1 — Standalone app (recommended)

Download the latest build for your platform from the
[Releases page](https://github.com/TheBrickalista/Pycrograph/releases/latest).

**macOS** — download `Pycrograph-vX.Y.Z-macOS.zip`, unzip it and move
`Pycrograph.app` to Applications. The app is signed and notarized by Apple.
It needs **macOS 15 or later on Apple Silicon** (PySide6 6.11's binding modules
require macOS 15). On an Intel Mac with macOS 15 or later, use Option 2.

**Windows** — download `Pycrograph-vX.Y.Z-Windows.exe` and run it. The
executable is not code-signed yet, so Windows SmartScreen warns on first run:
click **More info → Run anyway**.

Pre-releases (versions such as `0.2.0b1`) appear on the Releases page marked
*Pre-release*, with the same signed apps; they are not published on PyPI.

### Checking a download

Every release build runs `--selfcheck` on the exact file you download
(rendering and export of a synthetic image, no network). You can run it too:

```bash
/Applications/Pycrograph.app/Contents/MacOS/Pycrograph --selfcheck
```

## Option 2 — PyPI

```bash
python -m pip install pycrograph
pycrograph
```

Python 3.11 or later. A virtual environment is recommended.

## Option 3 — From source

```bash
git clone https://github.com/TheBrickalista/Pycrograph.git
cd Pycrograph
uv sync --extra dev        # or: pip install -e ".[dev]"
uv run python -m pycrograph
```

## Where your data lives

Projects, the operation log and caches live in `~/.pycrograph` (override with
the `PYCROGRAPH_WORKSPACE` environment variable). Raw images are **not copied**:
they are registered by reference and read where they are (see
{doc}`concepts/provenance`).
