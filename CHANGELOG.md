# Changelog

All notable changes to Pycrograph are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-18

Initial scaffold (session 1).

### Added
- Repository layout, tooling and CI mirroring PysternBlot 1.3.0 (uv, pytest on
  Linux + macOS, pyflakes, pip-audit, CodeQL, Dependabot, Read the Docs, GPLv3).
- Reader layer (`pycrograph/readers/`): `ImageSource` interface exposing
  (T, Z, C, Y, X) with lazy `get_plane`; MetaMorph TIFF (XML ImageDescription),
  MetaMorph STK (UIC1/2/3: calibration, Z distance, timestamps, wavelengths,
  interleaved channels), plain/ImageJ TIFF. JPEG rejected, 8-bit accepted with
  a recorded warning, float images refused for now.
- Pydantic models: Project, Dataset, ImageRef, ImageMetadata, ChannelSettings,
  ImageDisplay, Panel, Figure, PagePreset, Overlay, OperationLogEntry.
- Import by reference with background SHA-256, source checks (missing /
  changed / modified), hash-verified relink and drive-prefix relink, optional
  copy into the project (post-copy verification).
- Display pipeline: window, gamma, invert, LUT, additive composite; 90°
  rotation and flips; per-channel saturation with 3×3 erosion.
- Main window: library tree with thumbnails and annotations, zoom/pan viewer
  with T/Z navigation, per-channel inspector; one log entry per gesture.
- Shared-core candidates copied from PysternBlot: `logchain.py`,
  `jsonsafe.py`, `integrity.py` (adapted), `ui/zoomable_graphics_view.py`,
  `ui/levels_histogram.py`.
