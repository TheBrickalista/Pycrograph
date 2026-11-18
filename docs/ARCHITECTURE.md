# Architecture

Pycrograph mirrors PysternBlot's layout: a flat `pycrograph/` package (no
`src/`), Pydantic models serialised to one `project.json` per project, a
hash-chained operation log, and a PySide6 `QGraphicsView` UI.

## Hard rules

- **Figures only, no analysis.** No segmentation, measurements or ROI statistics.
- **Display-only transforms.** Raw pixels are never modified; everything is
  re-rendered from the raw file + saved settings.
- **Local only.** No server, no database, no network.
- **Lazy loading.** Target machine: M1, 8 GB RAM; a MetaMorph time-lapse is
  ~1.1 GB. A reader returns one plane at a time (memory-map or per-page read).

## Modules

| Module | Role |
|---|---|
| `models.py` | `Project › Dataset › ImageRef`, `ChannelSettings`, `ImageDisplay`, `Figure` (`PageSetup`, `legend`), `Panel` (`PanelBorder`, `InsetStyle`, `Shape`), `OperationLogEntry`. `PROJECT_FORMAT_VERSION = 2` |
| `migrations.py` | Raw-dict migrations of `project.json` applied before validation (1 → 2: `Figure.page` `PagePreset` → `PageSetup`) |
| `figures.py` | Figure editing without Qt: create/rename figures, panels from library images (copy of display settings), move/resize/delete, `FigureEditor` gestures with snapshot undo/redo |
| `viewport.py` | Viewport = crop (pure geometry): uniform scale, zoom % (100 % = whole image fitted), pan, resize at constant scale, rotation about the crop centre, orientation mapping, where the image sits in its panel |
| `render.py` | `PanelRenderer`: raw plane(s) or max projection → crop by array slicing → `render_composite` → orientation; LRU cache keyed by source hash + settings |
| `readers/` | `ImageSource` interface (T, Z, C, Y, X; lazy `get_plane`). `tiff.py`: plain TIFF, MetaMorph TIFF (XML), MetaMorph STK (UIC tags). `metamorph.py`: metadata parsing |
| `display.py` | window → gamma → invert → LUT → additive composite → RGB8; orientation; saturation; auto-window |
| `storage.py` | `Workspace` (atomic saves), import **by reference**, SHA-256, `check_image`, relink (single + drive-prefix), copy into project, annotations, thumbnails |
| `logchain.py` | Hash-chained log — **shared-core** (verbatim from PysternBlot) |
| `jsonsafe.py` | Strict JSON — **shared-core** (verbatim) |
| `integrity.py` | Integrity report (JSON) — **shared-core** (adapted) |
| `ui/main_window.py` | Tabs Home · Library · Figures · Preferences · About. Library tab = tree (left) · viewer (centre) · inspector (right); gesture commit |
| `ui/home_tab.py` | Project list (new / open / archive / restore), figures of the open project |
| `ui/figures_tab.py`, `ui/figure_canvas.py` | Figure selector, page canvas in mm at journal width, panel items (select, rubber band, move, resize handle), undo/redo, live-gesture commit, Preview · Info inspector |
| `ui/panel_inspector.py` | Preview tab: edits all selected panels with mixed states; viewport mini-view, zoom, rotation/flips, T/Z + max projection, channel controls by index, copy/paste rendering and viewport, image defaults, replace image |
| `ui/info_tabs.py` | Preferences (default page preset, read-only for now) and About |
| `ui/library_panel.py` | Virtual tree, search/rating filter, metadata, tags / rating / key–values |
| `ui/channel_controls.py` | One channel: visibility, LUT, histogram gates, Black/White, gamma, invert, saturation; mixed state for multi-panel editing |
| `ui/workers.py` | Background hashing (`QThreadPool`, one file at a time) |
| `ui/zoomable_graphics_view.py`, `ui/levels_histogram.py` | **shared-core** (verbatim from PysternBlot) |

Files copied from PysternBlot carry a `# SHARED-CORE candidate (from PysternBlot: <path>)`
header (checked by `tests/test_project_hygiene.py`). They are extracted into a
common package once both apps use them; PysternBlot itself is not modified.

## Images are registered by reference

`register_image()` stores the absolute path, size, mtime, reader + version and
parsed metadata; the SHA-256 is computed afterwards in the background
(`set_image_hash`). `check_image()` is cheap (exists + size + mtime);
`verify_hash=True` re-reads the file. A relink is only accepted when the new
file hashes to the recorded SHA-256. When the external drive is remounted
under another name (`/Volumes/LaCie` → `/Volumes/LaCie 1`), relinking one
image offers the same prefix change for every other missing image.

## Display pipeline

For each visible channel: `raw → [black, white] → x**(1/gamma) → invert → × LUT colour`,
summed over channels, clipped, quantised once to RGB8. The window/gamma curve
is precomputed into a 65 536-entry table per channel. Rotation (multiples of
90°) and flips act on the final RGB; nothing is resampled.

Saturation is computed on the raw plane against `2**bit_depth − 1` (MetaMorph
`bits-per-pixel`, else the dtype width), with PysternBlot's 3×3 erosion to
separate solid saturated structures from hot pixels.

## Operation log

Every model change is logged once per gesture (slider release, field commit,
or 700 ms without a tick), then the project is saved: `project.json` and the
log never disagree. Field paths look like `display.channels[1].white`.

## Figures

Scene unit = 1 mm; pages are stacked vertically (`Panel.page`). The canvas
never writes the model: on mouse release it reports changed geometry once,
and `FigureEditor` applies it as one gesture — snapshot before, mutate, one
log entry per panel added / removed / changed (`target_type="panel"`,
`asset_sha256` = the panel's image), push the (before, after) snapshots on
the undo stack, save. Undo/redo restore a snapshot and are logged the same
way (`figure.undo` / `figure.redo`). The undo stack is in memory only.

Continuous controls (sliders, mini-view drag, wheel zoom) use
`FigureEditor.edit(..., final=False)`: every tick changes the model and
redraws; the gesture is committed on release, after 700 ms idle, or when
anything else happens. Log field paths name the changed leaves
(`channels[1].white`, keyed by channel index; `crop.x`).

The viewport is `Panel.crop` (raw pixels). The panel shows it at one
uniform scale; the canvas draws only the part of the crop that lies on the
image, at its proportional place, clipped to the panel — the image is never
stretched and the rest is page background.

On screen, panels are decimated by an integer stride to at most 1024 px per
side (no interpolation); export will render at native crop resolution.

## Format migrations

`load_project_file` parses the JSON, refuses newer versions, applies
`migrations.migrate_project_dict` and validates. A migration is logged
(`project.migrate`) in memory; the file is only rewritten — new format and
that entry together — at the next save.

## STK axis inference

An STK stores N planes in one IFD. If the per-plane wavelengths (UIC3) repeat
with a period c > 1 the planes are channel-interleaved (C = c). The remaining
axis is Z when the Z distances (UIC2) between plane groups are non-zero, else
T. Timestamps come from UIC2 creation date/time.
