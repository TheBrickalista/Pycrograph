# Changelog

All notable changes to Pycrograph are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Figure editor, steps 1 to 5 (docs/design/figure-editor.md §6).

### Added (step 5: insets and shapes)
- Insets & Shapes inspector tab.
- Create inset: draw a rectangle on the selected panel → a new panel showing
  that region (raw pixels) at 2× the parent's magnification, right of the
  parent (snapped, on the page). Several insets per parent, numbered and
  coloured to match (rectangle on the parent, border and number on the inset).
- The rectangle on the parent is derived from the inset's crop: drag or
  resize it (corner, aspect kept) to change the inset; pan / zoom / resize
  the inset and the rectangle follows; any rotation / flip of either.
- Follow the parent's T / Z / projection and / or channel settings
  (InsetStyle flags, toggled in the tab), applied in the parent's gesture.
- Inset style: colour, width, rectangle on the parent, matching border.
  "Make ordinary panel" detaches an inset.
- Copy / paste of a parent with its insets keeps the link in the pasted set.
- Shapes (arrow, line, rectangle, ellipse) in raw-image pixels: draw, click
  to select, drag to move, colour / stroke / arrowhead (page points), delete,
  copy / paste between panels (same raw coordinates); clipped to the panel;
  vector items tagged by kind.

### Changed (step 4 follow-ups)
- `[time]` without a unit uses one unit per lock group / montage, chosen from
  its largest time; single panels unchanged.
- Shared rendering (lock propagation, Lock selection, condition grid) warns
  when the same channel index has a different channel name or wavelength.
- Lock selection asks for confirmation when the panels' levels / LUTs differ.
- Deleting a parent also clears its insets' style (they become ordinary).

### Added (step 4: labels, scale bar, border)
- Labels tab: add / edit / delete labels on all selected panels; positions
  top, bottom, left, right, left-vertical, right-vertical and the four inside
  corners; outside labels stack without overlapping, inside labels keep a
  margin (inside the border); font family and size from the page preset,
  overridable per label; colour per label (auto: black outside, white inside).
- Tokens resolved at render time: [channels] (stain or channel name, coloured
  like its LUT), [time], [time:s], [time:min], [time:hh:mm] (with
  Panel.time_offset_s), [z], [z:um] (with Panel.z_offset), [zoom], [image],
  [dataset], [tags], [kv:Key]. Missing values are drawn as "?" and listed in
  the status bar and the Labels tab.
- "Labels from key–value…" adds [kv:<key>] to all selected panels at once.
- Scale bar: length and unit (µm, nm, mm), corner, colour, thickness, text,
  font size, text above / below; length follows zoom, resize, rotation and
  Align magnification; greyed with the reason for uncalibrated images;
  non-isotropic pixels use the axis along the bar (and say so).
- Panel border (colour, width) drawn inside the panel edge.
- Labels, scale bar and border are vector items in page mm, tagged by kind
  for the export.
- Copy / paste panels (Ctrl+C / Ctrl+V on the canvas): labels and settings
  kept, pasted panels unlocked. Split channels, montage and condition grid
  copy labels; the condition grid adds row labels ([image] or [kv:…]) and
  [channels] column labels, with room reserved for them.

### Added (lock groups)
- Lock propagation: an edit of a channel's LUT, black, white, gamma or invert
  on a locked panel applies to the same channel of every panel of its group
  (clamped per image, panels lacking the channel skipped), in one gesture.
  Visibility, T/Z, projection, crop, orientation and labels are never shared.
- Preview tab: lock indicator, Lock selection (syncs to the first panel),
  Unlock, Select group.

### Changed (condition grid)
- Rows take the first row's per-channel rendering by default (crop, T/Z and
  projection stay per image); "Condition grid (keep each image's rendering)"
  keeps them, with one lock group per row.

### Changed (CI)
- CodeQL analysis is skipped on private repositories.

### Added (step 3: layout)
- Layout toolbar: align left / right / top / bottom / centres, distribute
  horizontally / vertically, Grid (rows and columns from the current
  positions, configurable gap, top-left panel fixed), same width / height /
  both, Align magnification (resizes panels so µm per page-mm matches the
  top-left panel; crops unchanged; uncalibrated panels skipped).
- Arrow keys nudge the selection by 0.1 mm (Shift: 1 mm); a burst of
  presses is one gesture.
- Alignment guides while dragging (edges and centres of other panels and of
  the page), with snapping; the selection moves as one block.
- Page setup dialog: journal preset, size, orientation, pages, background,
  grid and snap (one figure-level log entry).
- Preferences: editable journal presets stored as data in
  `<workspace>/preferences.json` (built in: Nature single 89 mm and double
  183 mm column, max height 170 mm, 300 dpi, Arial 8 pt), default preset for
  new figures, layout gap.
- Auto-layouts: split channels (one panel per channel + merge), montage of
  T points or Z slices (row or column), condition grid (rows = images of the
  selected panels, columns = each channel then the merge); each layout's
  panels share one `lock_group`.
- Faint dashed outline on unselected panels (canvas only: decorations are
  off for export).

### Changed (step 3)
- Multi-panel pan converts the displacement through each panel's own
  orientation (it used the first panel's).
- Figure-level log entries use leaf paths (`page.width_mm`).

### Fixed (step 3)
- The Preview inspector could show the previous selection's widgets until
  the event loop deleted them.

### Added (step 2: Preview)
- Inspector tabs Preview · Info. Preview edits all selected panels; values
  that differ show a mixed state (blank field, partially checked box,
  hidden slider handle, “—”), and touching a control sets every panel.
- Channel controls (reused `ChannelControls`) matched by channel index;
  panels whose image lacks the channel are skipped (status bar count);
  levels range = largest full scale of the selection, clamped per image.
  Histogram, saturation and Auto computed on the panel's crop.
- T and Z sliders, max-intensity Z projection with a range.
- Viewport = crop (§2.4): mini-view of the whole image with the crop
  rectangle (drag = pan, wheel = zoom), zoom % (100 % = whole image),
  Fit; rotation 90° (panel kept, crop turned) and flips.
- Copy / Paste rendering (channel settings by index) and viewport
  (centre, scale, orientation); Reset to image defaults; Save as image
  defaults (logged on the image, library thumbnail refreshed); Replace
  image with a compatibility check (channel count, calibration when a scale
  bar is shown; warnings for size, bit depth, pixel size, T/Z clamping).
- Continuous gestures: every tick redraws, one undo step and one log entry
  per panel when released or after 700 ms idle. Log fields are leaf paths
  (`channels[1].white`, `crop.x`).

### Changed (step 2)
- Resizing a panel extends or shrinks the crop at constant scale (crop
  centre kept) instead of scaling the image; the handle resizes freely,
  Shift keeps the aspect ratio.
- The canvas draws the image at one uniform scale, clipped to the panel;
  where the crop runs off the image the page background shows (it was
  drawn into the whole panel rectangle).
- `Crop.x` / `Crop.y` may be negative (crop past the image's top/left).

### Added (step 1)

### Added
- Top-level tabs: Home (projects: new / open / archive / restore; figures of
  the open project), Library (the session-1 view), Figures, Preferences
  (default page preset, read-only), About (version, licence, citation).
- Figures tab: figure selector (New / Rename), page canvas in millimetres at
  journal width (Nature double column, 183 × 170 mm by default), panels
  rendered from the raw files; click, Ctrl/Shift-click, rubber-band and
  Select All; drag to move (1 mm grid snap); corner handle to resize
  (aspect ratio kept); Delete; Undo / Redo (snapshot based); info pane with
  crop and effective resolution (warns below the preset dpi).
- “Add to figure” (library context menu and toolbar): one panel per image
  with a copy of the image's current display settings; creates “Figure 1”
  when the project has no figure.
- `PanelRenderer` (`render.py`): crop by array slicing, then
  `display.render_composite`, then orientation; max Z projection; LRU cache.
- `figures.py`: `FigureEditor` — one undo step per gesture and one
  operation-log entry per affected panel (`figure.panel.add/move/resize/delete`,
  `figure.undo/redo`), then save.
- Models v2: `PageSetup` (preset, page size, orientation, pages, background,
  grid, snap), `PanelBorder`, `InsetStyle`, `Shape`; `Panel.page`, `border`,
  `inset_style`, `time_offset_s`, `z_offset`, `shapes`; label positions
  `left-vertical` / `right-vertical`; `ScaleBar.font_size_pt` and
  `label_position`; `Figure.legend`.

### Changed
- `PROJECT_FORMAT_VERSION` is 2. Version-1 projects are migrated on load
  (`Figure.page` `PagePreset` → `PageSetup`); the migration is recorded as a
  `project.migrate` log entry and written at the next save.
- About opens the About tab instead of a dialog.

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
