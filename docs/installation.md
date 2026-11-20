# Installation

Pycrograph runs on macOS and Windows (and anywhere Python and PySide6 run).

Pycrograph is in **beta**: 0.x releases are published on GitHub as
*pre-releases*. Version **1.0.0** will be the first stable release, also
published on PyPI and archived on Zenodo with a DOI.

| Method | Best for | Python needed? |
|---|---|---|
| **GitHub release assets** | Most users | No |
| **From source** | Python users, Intel Macs, contributors | Yes (≥ 3.11) |
| **PyPI (`pip install`)** | From 1.0.0 | Yes (≥ 3.11) |

## Option 1 — GitHub release assets (recommended)

Download the build for your platform from the
[Releases page](https://github.com/TheBrickalista/Pycrograph/releases). Beta
releases are marked *Pre-release*: pick the most recent one (GitHub's
"Latest" link skips pre-releases).

**macOS** — download `Pycrograph-vX.Y.Z-macOS.zip`, unzip it and move
`Pycrograph.app` to Applications. The app is **signed and notarized** by
Apple, so it opens without a security warning. It needs **macOS 15 or later
on Apple Silicon** (PySide6 6.11's binding modules require macOS 15). On an
Intel Mac with macOS 15 or later, install from source (Option 2).

**Windows** — download `Pycrograph-vX.Y.Z-Windows.exe` and run it. The
executable is **not code-signed** yet, so Windows SmartScreen warns on first
run ("Windows protected your PC"): click **More info → Run anyway**.

### Checking a download

Every release build runs `--selfcheck` on the exact file you download
(rendering and export of a synthetic image, no network). You can run it too:

```bash
/Applications/Pycrograph.app/Contents/MacOS/Pycrograph --selfcheck
```

## Option 2 — From source

```bash
git clone https://github.com/TheBrickalista/Pycrograph.git
cd Pycrograph
git checkout v0.2.0        # or stay on main for the latest code
uv sync --extra dev        # or: pip install -e ".[dev]"
uv run python -m pycrograph
```

Python 3.11 or later. A virtual environment is recommended (uv creates one).

## PyPI (from 1.0.0)

Betas are not uploaded to PyPI. From the first stable release on:

```bash
python -m pip install pycrograph
pycrograph
```

## Where your data lives

Projects, the operation log and caches live in `~/.pycrograph` (override with
the `PYCROGRAPH_WORKSPACE` environment variable). Raw images are **not copied**:
they are registered by reference and read where they are (see
{doc}`concepts/provenance`).
