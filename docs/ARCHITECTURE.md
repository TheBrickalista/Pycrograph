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
| `models.py` | `Project › Dataset › ImageRef`, `ChannelSettings`, `ImageDisplay`, `Figure` (`PageSetup`, `legend`), `Panel` (`PanelBorder`, `InsetStyle`, `Shape`), `OperationLogEntry`. `PROJECT_FORMAT_VERSION = 4` (older projects migrated on load, `migrations.py`) |
| `migrations.py` | Raw-dict migrations of `project.json` applied before validation (1 → 2: `Figure.page` `PagePreset` → `PageSetup`) |
| `figures.py` | Figure editing without Qt: create/rename figures, panels from library images (copy of display settings), move/resize/delete, `FigureEditor` gestures with snapshot undo/redo |
| `viewport.py` | Viewport = crop (pure geometry): uniform scale, zoom % (100 % = whole image fitted), pan, resize at constant scale, rotation about the crop centre, orientation mapping, where the image sits in its panel |
| `layout.py` | Layout tools (pure): align, distribute, grid with gap, nudge, same size, align magnification (µm per page-mm), auto-layouts (split channels, montage, condition grid) with `lock_group` |
| `preferences.py` | App preferences in `<workspace>/preferences.json`: journal presets (data), default preset, layout gap. Not project data, not logged |
| `archive.py` | `.pcarchive` export / import (light: OME-TIFF extracts of what the figures use; complete: raw files), streamed + hashed, untrusted-input checks — **shared-core** helpers (adapted) |
| `consistency.py` | Pre-export check (§2.9), pure: per row / column / lock group and per panel; `Issue(severity, code, message, panel_ids)` |
| `export.py` | Figure export (PDF, SVG, TIFF, PNG) from the shared scene, source-data export (OME-TIFF), source verification, metadata embedding, one log entry per export |
| `provenance.py` | Export sidecar (pure): per panel source + hash, crop as rendered, orientation, channels, flags, resolved labels, scale-bar basis, insets, shapes |
| `insets.py` | Dynamic insets (pure): create (2× magnification, placement), rectangle on the parent derived from the inset's crop, edit it back, follow-parent sync, links on copy/paste; replication (compatibility, stage positions, region / size links; `sync_group` keeps a linked group one inset repeated, `group_invariant_issues` checks it) |
| `labels.py` | Label tokens resolved at render time ([channels], [time…], [z…], [zoom], [image], [dataset], [tags], [kv:Key]); unresolved → "?" + reported |
| `selfcheck.py` | `--selfcheck`: offline check of an installed / packaged app (metadata, resources, Qt plugins, render + export), run by the release builds |
| `scalebar.py` | Scale bar length on the page from the calibration and the viewport scale; reason when unavailable; non-isotropic pixels |
| `render.py` | `PanelRenderer`: raw plane(s) or max projection → crop by array slicing → `render_composite` → orientation; LRU cache keyed by source hash + settings |
| `readers/` | `ImageSource` interface (T, Z, C, Y, X; lazy `get_plane`); `open_image_ref` (original or light-archive extract, `extract.py`). `tiff.py`: plain TIFF, MetaMorph TIFF (XML), MetaMorph STK (UIC tags). `metamorph.py`: metadata parsing |
| `display.py` | window → gamma → invert → LUT → additive composite → RGB8; orientation; saturation; auto-window |
| `storage.py` | `Workspace` (atomic saves), import **by reference**, SHA-256, `check_image`, relink (single + drive-prefix), copy into project, annotations, thumbnails |
| `logchain.py` | Hash-chained log — **shared-core** (verbatim from PysternBlot) |
| `jsonsafe.py` | Strict JSON — **shared-core** (verbatim) |
| `integrity.py` | Integrity report (JSON + HTML, exports re-checked) — **shared-core** (adapted) |
| `ui/main_window.py` | Tabs Home · Projects · Library · Figures · Preferences · About (opens on Home). Library tab = tree (left) · viewer (centre) · inspector (right); gesture commit |
| `ui/home_tab.py` | Start page (PysternBlot style): logo, version, licence, big buttons to the entry points; emits intents only |
| `ui/projects_tab.py` | Project list (new / open / archive / restore), figures of the open project |
| `ui/figures_tab.py`, `ui/figure_canvas.py` | Figure selector, page canvas in mm at journal width, panel items (select, rubber band, move, resize handle), undo/redo, live-gesture commit, Preview · Info inspector |
| `ui/archive_dialog.py` | Archive level and optional contents |
| `ui/export_dialogs.py` | Consistency dialog (click selects panels; "Export anyway") and export settings dialog |
| `ui/scene_builder.py` | Panel visual + overlay builder shared by canvas and export; `export_scene` (decorations off, native rasters) |
| `ui/replicate_dialog.py` | Replicate-inset dialog (targets, compatibility, mode, keep linked) |
| `ui/insets_inspector.py` | Insets & Shapes tab: create inset, inset style and follow flags, shapes (draw, list, style, delete, copy/paste) |
| `ui/labels_inspector.py` | Labels tab: labels, scale bar and border of all selected panels |
| `ui/panel_overlays.py` | Labels, scale bar, border, inset rectangles / borders / numbers and shapes as vector child items of each panel (page mm), tagged by kind for the export; interactive inset rectangles and shapes |
| `ui/page_setup.py` | Page setup dialog (preset, size, orientation, pages, background, grid, snap) |
| `ui/panel_inspector.py` | Preview tab: edits all selected panels with mixed states; viewport mini-view, zoom, rotation/flips, T/Z + max projection, channel controls by index, copy/paste rendering and viewport, image defaults, replace image |
| `ui/info_tabs.py` | Preferences (editable journal presets, default preset) and About |
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

Layout tools run as one `FigureEditor.layout()` gesture each
(`figure.layout.*`, one entry per panel changed or added). The canvas moves
the selection itself as one block: the grabbed panel snaps to alignment
guides (edges/centres of other panels and of the page) or else to the grid,
and every selected panel gets the same offset. Outlines, selection,
handles, guides and the page border are decorations
(`FigureScene.decorations`), off for exports.

The viewport is `Panel.crop` (raw pixels). The panel shows it at one
uniform scale; the canvas draws only the part of the crop that lies on the
image, at its proportional place, clipped to the panel — the image is never
stretched and the rest is page background.

On screen, panels are decimated by an integer stride to at most 1024 px per
side (no interpolation); export will render at native crop resolution.

### Lock groups

`Panel.lock_group` shares **black, white, gamma, LUT and invert per channel
index** between the panels of a group — never visibility, T/Z, projection,
crop, orientation or labels. `FigureEditor.edit()` propagates: locked fields
changed on the first edited panel of a group (or the typed values, passed as
`lock_values`) are applied to every other member, clamped to each image's
bit depth; members lacking the channel are skipped. It happens inside the
same gesture: one undo step, one log entry per panel changed. Locking a
selection syncs the others to its first panel.

### Labels, scale bar, border

Overlays are rebuilt from the model whenever a panel changes (and live while
resizing): child `QGraphicsTextItem` / `QGraphicsRectItem`s of the panel item
in page mm, tagged with `data(KIND_KEY)` = `label`, `scalebar`,
`scalebar-text` or `border`, so export can keep text as text. Each panel item
holds Python references to its overlays (PySide does not keep a child of a
Python-subclassed item alive on its own).

### Insets and shapes

An inset is a Panel with `inset_of` + `InsetStyle`; its crop is the only
record of the region. The rectangle on the parent is derived at render
(`insets.rect_on_parent`, through `viewport.raw_rect_to_panel`), and
editing it writes the inset's crop (`insets.set_from_parent_rect`). Follow
flags are applied by `FigureEditor.edit` after the edit and lock
propagation, in the same gesture. Shapes are stored in raw pixels and drawn
through `viewport.raw_to_panel`, so they follow crop, zoom and orientation;
stroke widths are page points. Shapes and inset rectangles sit in a clipping
container the size of the panel. Interactive overlay items only react when
their panel is selected, and commit after their mouse handler returns (the
commit rebuilds the overlays).

Tests: `tests/conftest.py` makes any unexpected modal dialog fail the test
(offscreen, a dialog would block forever).

## Export and provenance

`ui/scene_builder.export_scene` builds the same scene as the canvas
(`FigureScene.populate`) with editor decorations off and every panel raster
at native crop resolution (`PanelRenderer.render(max_side=None)`: only the
part of the crop on the image, placed with `viewport.image_rect_mm`).
`export.py` renders a page rect of that scene into `QPdfWriter` (lossless
image hint, fonts embedded, XMP), `QSvgGenerator` (+ `<metadata>`) or a
`QImage` at the chosen dpi (TIFF via tifffile with resolution tags, PNG with
pHYs + text chunks). Order: verify sources → write the sidecar
(`provenance.py`) and hash it → write the file(s) embedding that hash →
hash them → one log entry (`export.figure` / `export.sources`). The
integrity report lists every logged export and re-hashes its files.

## Archives

`.pcarchive` = zip: `pcarchive/manifest.json` (SHA-256 + size of every
member, SHA-256 of the project state, level, log-chain status) and
`pcarchive/project/` mirroring a project directory (`project.json`, plus
`images/` for a complete archive or `extracts/` for a light one), optional
`exports/` and `reports/`. The archived project.json has its image refs
relinked inside the archive and one `project.archive` log entry saying so;
the live project logs `export.archive`. A light extract keeps only the
planes and region the figures use; its ImageRef keeps the original metadata
and SHA-256 and gains `extract`, and `readers/extract.ExtractSource`
presents it with the original geometry (zeros outside the region, planes not
archived refused). Import: container checks, every member streamed into a
staging directory outside the workspace and hashed, project-state hash, log
chain and image records checked; then a new project is created and
`project.import_archive` logged. Image members are stored uncompressed (raw
data does not compress), so the compression-ratio limit (1000:1) flags bombs;
text members have their own cap (200:1, 64 MB) and the exporter stores a text
member uncompressed when it would come near it. Free disk space (members + 5 %)
is checked in the staging and workspace locations first.

Provenance of restored light extracts: `provenance.extract_block` (export
sidecar, source-data sidecar, PDF info page, integrity report) says the
original is not present, gives the extract's own SHA-256 (the one verified)
and the original's as recorded only; the consistency check adds an info item.

## Release pipeline

Mirrors PysternBlot (files marked shared-core). `publish.yml` runs on a
published GitHub release (or manually as a dry run): tests → sdist / wheel
(`uv build` with `ci/build-constraints.txt`, hash-pinned setuptools) + `twine
check` → `build-macos.yml` and `build-windows.yml` (called only after the tests
pass; they attach `Pycrograph-<tag>-macOS.zip` / `-Windows.exe` to the
release) → PyPI trusted publishing (environment `pypi`), skipped for
pre-releases. `scripts/make_version_file.py` validates the version (x.y.z or a
PEP 440 a/b/rc pre-release) for the release-tag check, Info.plist and the
Windows version resource. Both builds run `--selfcheck` on the artifact.
`tests/test_workflows_hardening.py` guards permissions, pinned actions and
runners, credentials, event data in shell, and the release gating.

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
