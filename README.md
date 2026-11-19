# Pycrograph

**Publication figures from fluorescence microscopy images — non-destructive and provenance-tracked.**

Pycrograph is the imaging sister of [PysternBlot](https://github.com/TheBrickalista/PysternBlot)
(Western blot figures). It keeps what OMERO.figure does well — figures re-rendered
from raw data with recorded display settings — without a server.

> **Status: alpha (0.2.0).** Library, MetaMorph readers, the per-channel
> display pipeline, the figure editor (layout, labels, scale bars, insets,
> shapes), consistency check, provenance-tracked export and `.pcarchive`
> archives work (see [Roadmap](Roadmap.md)).

## Principles

- **Figures only.** No segmentation, measurements or intensity statistics.
- **Raw pixels are never modified.** Levels, gamma, LUTs, rotation and flips
  are display settings re-applied at every render.
- **Local only.** No server, no database, no network.
- **Provenance as in PysternBlot.** SHA-256 of every source file, hash-chained
  operation log, integrity report; 8-bit sources flagged, JPEG rejected,
  non-linear gamma flagged.
- **Big files, small laptop.** Images are registered *by reference* (not copied)
  and read one plane at a time, so a 1 GB time-lapse opens on an 8 GB Mac.

## What works now

- Projects › Datasets › Images (OMERO-style virtual tree; one file can sit in
  several datasets), tags, key–value pairs, ratings, search.
- MetaMorph TIFF (XML metadata: pixel size, illumination, wavelength,
  objective/NA, exposure, acquisition time) and STK (multi-T / multi-Z /
  interleaved channels), plain and ImageJ TIFF.
- Background SHA-256; missing/changed/modified detection; hash-verified relink,
  including "drive was remounted as `/Volumes/LaCie 1`" for all images at once.
- Per channel: on/off, LUT (named or custom colour), histogram with draggable
  gates, Black/White typed or dragged, gamma, invert, saturation badge,
  free-text stain/antibody. T/Z navigation, 90° rotation, flips.
- Integrity report (JSON).

## Install

| Platform | Download | Notes |
|---|---|---|
| **macOS** | `Pycrograph-vX.Y.Z-macOS.zip` from the [latest release](https://github.com/TheBrickalista/Pycrograph/releases/latest) | macOS 15 or later, Apple Silicon. Signed and notarized: unzip, move to Applications, open. |
| **Windows** | `Pycrograph-vX.Y.Z-Windows.exe` from the [latest release](https://github.com/TheBrickalista/Pycrograph/releases/latest) | Not yet code-signed: Windows SmartScreen shows a warning on first run — click **More info → Run anyway**. |
| **Python (any OS)** | `pip install pycrograph` | Python 3.11 or later; also for Intel Macs (macOS 15 or later). Run with `pycrograph`. |

macOS versions before 15 are not supported (PySide6 6.11's binding modules
require macOS 15). Pre-releases (e.g. `0.2.0b1`) are published as GitHub
pre-releases with signed apps, not on PyPI. Full documentation:
<https://pycrograph.readthedocs.io>.

### Development

```bash
git clone https://github.com/TheBrickalista/Pycrograph.git
cd Pycrograph
uv sync --extra dev        # or: pip install -e ".[dev]"
uv run python -m pycrograph
```

Python ≥ 3.11. The workspace lives in `~/.pycrograph` (override with
`PYCROGRAPH_WORKSPACE`).

## Tests

```bash
uv run pytest
# with the real MetaMorph reference image:
PYCROGRAPH_SAMPLE_TIFF="/Volumes/LaCie/…/Nuc_ER_ER.tif" uv run pytest -m sample
```

## License

GPL-3.0-only. © 2026 Etienne Boulter (IRCAN — Inserm, CNRS, Université Côte d'Azur).
