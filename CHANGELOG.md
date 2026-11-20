# Changelog

All notable changes to Pycrograph are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Inset numbers can be hidden.** `InsetStyle.show_number` (default on),
  with a *Show number* checkbox in the Insets & Shapes tab next to the other
  style options. It applies to every selected inset in one gesture, shows a
  mixed state when they differ, and is per inset (not shared through linked
  groups).
  - When off, no number is drawn on the inset (a top-left label moves up to
    the margin) nor next to its rectangle on the parent. Colours, border and
    rectangle are unchanged, on screen and in every export.
  - The provenance sidecar's inset record adds `number_shown` next to
    `number`.
- Project format **4**: the migration writes `show_number: true` into
  existing inset styles (logged as `project.migrate`).
- `pycrograph/ui_text.py`: `plural(n, word)` / `noun(n, word)` for
  user-facing wording, with a test guarding against `(s)` in message strings.
- **Mixed bit depths in a lock.**
  - *Lock selection* adds "These images have different bit depths (8-bit:
    panel 2; 16-bit: panels 1, 3). Equal levels do not mean equal
    intensities across bit depths or cameras." to its confirmation, and asks
    even when no setting differs.
  - Auto-layouts whose new lock group spans images of different bit depths
    (condition grid, shared rendering) show the warning in the status bar.
  - New consistency rule `bit_depth` (warning): "lock group (…, panels …):
    mixes 8-bit and 16-bit images", which selects the panels.
  - Propagation is unchanged: the typed value stays the reference, clamped
    per image.
- **Skipped channels named.** When a locked edit can't reach panels that lack
  the channel, the status bar says "Channel 3 applied to 2 locked panel(s); 1
  skipped (no channel 3: panel 4)". Nothing extra is logged
  (`FigureEditor.last_lock_skipped`, `figures.skipped_note`).
- Locks guide: case (b) shows the bit-depth warning (new screenshot
  `locks-depth.png`) and the check; troubleshooting shows the skipped-panel
  message.
- Docs: how-to page **Locks, step by step** (`docs/guide/locks.md`). It covers
  what a lock shares (per channel number) and what stays per panel; the
  automatic locks (split row, montage, condition grid shared / own
  rendering); locking by hand (reference panel, confirmation, channel
  mismatch warning, Unlock, Select group); three worked cases (control vs
  treated, 8-bit with 16-bit, a different stain in channel 1); locks vs inset
  links vs *Follow the parent's channels*; the consistency check and the
  sidecar; and troubleshooting. It is linked from the Figures guide and from
  *Locks and insets*.
- `scripts/make_lock_screenshots.py` (reuses the inset script's synthetic
  images and English locale) regenerates `docs/_static/img/locks-*.png`. It
  has a smoke test and a locale test.
- Provenance sidecar: per panel `lock_kind` (*split row*, *montage*,
  *condition grid*, *manual lock*) next to `lock_group`.
- Docs: how-to page **Insets, step by step** (`docs/guide/insets.md`). It
  covers creating insets (on one panel or several), replicating them (table
  columns, Same region vs Same size, the registration warning, Keep linked),
  three worked cases (split channels, MetaMorph files per wavelength, other
  cells or objectives), working with a linked group, and troubleshooting.
  It is linked from the Figures guide and from *Locks and insets*.
- `scripts/make_inset_screenshots.py`: regenerates the guide's screenshots
  (`docs/_static/img/insets-*.png`) offscreen from synthetic images, with a
  smoke test so it keeps working when the UI changes.
- Figures tab: **Add Images…** picker (`ui/add_images_dialog.py`) replaces
  the old drop-down. It shows the Library's tree (thumbnails, status, counts)
  with search, a minimum-rating filter and a preview pane. It supports
  multi-selection, and selecting a dataset means all its visible images.
  Panels are added in tree order, once each, in one gesture. Double-click adds
  one image and closes. Missing images are greyed and can't be selected.
  **Import from disk…** goes through the main window's import flow and
  pre-selects the new images. Size, collapsed datasets and the last dataset
  are remembered for the session. The action is enabled in projects with no
  images.
- `ui/image_tree.py`: one tree-filling helper shared by the Library tab and the
  picker, so the two can't drift apart. Library behaviour is unchanged.
- Dev dependency `pytest-qt` (≥ 4.5.0, `qt_api = "pyside6"`); UI tests for the
  picker.
- Release pipeline mirroring PysternBlot (shared-core, same pinned actions and
  hardening): `publish.yml` (tests → sdist / wheel with hash-pinned build
  constraints + `twine check` → signed macOS and Windows apps → PyPI trusted
  publishing, environment `pypi`; pre-releases are not uploaded to PyPI),
  `build-macos.yml` (arm64, macOS 15+, codesign with hardened runtime,
  notarization, stapling, bundle checks, asset `Pycrograph-vX-macOS.zip`),
  `build-windows.yml` (unsigned `.exe` with a version resource, asset
  `Pycrograph-vX-Windows.exe`).
- `scripts/make_version_file.py`: accepts PEP 440 pre-releases (`0.2.0b1`);
  OS bundle / exe versions use `x.y.z(.0)`, the full string stays in the
  About tab, release tag and asset names.
- `pycrograph --selfcheck [--selfcheck-out FILE]`: offline check of an
  installed or packaged app (metadata, resources, Qt plugins, render and
  export of a synthetic image), run by both app builds on the artifact.
- App icon (temporary placeholder, `pycrograph/resources/pycrograph_logo.png`).
- Release-notes template; README install section; documentation restructured
  (installation, quickstart, user guide, concepts, contributing, licence).
- Tests: workflow hardening (pinned SHAs, least privilege, no
  `pull_request_target`, release gating, signing secrets, entitlements),
  version file with pre-releases, selfcheck.

- Replicated insets: *Replicate to…* (Insets & Shapes tab) puts the selected
  inset on the other selected panels or on every panel of its parent's split /
  lock group; *Create inset* with several panels selected offers the same.
  Per target, the geometry is checked (same image, same geometry, different;
  MetaMorph stage positions equal / differ / unknown): *Same region* (same
  raw rectangle, compatible geometry only, warns that it assumes registered
  images) or *Same size* (same µm size, same relative centre, clamped).
  *Keep linked* puts the insets in a region group: editing one's region
  updates the others in the same undo step, one log entry per inset. Group
  shown in the inspector, *Unlink from group*; delete / detach / copy-paste
  keep groups consistent.

### Fixed
- Screenshot smoke tests (`test_inset_screenshots.py`,
  `test_lock_screenshots.py`) no longer depend on platform font metrics. They
  check that each image is non-null and at least 50 × 50 px; CI on Linux
  failed on a 400 × 99 confirmation dialog.
- Replicate-inset dialog: no longer errors when it opens with several target
  sets. A radio button's `toggled` signal fired before the table existed.
- Linked inset groups could diverge silently: resizing a member on the canvas
  or with `set_geometry`, the layout tools (same width / height, align
  magnification) and rotating or flipping a member left the others behind,
  and a rotation pushed its raw rectangle into siblings of the old
  orientation. A linked group is now one inset repeated (shared page size,
  rotation, flips and region; positions per member), enforced at every
  gesture commit and live tick whatever the path, in the same undo step;
  the first member acted on wins. Replicated copies take the source's size
  and orientation. The consistency check's group warning now covers every
  divergence (`insets.group_invariant_issues`).

### Changed
- **Insets no longer inherit the parent's scale bar.**
  - *Create inset* gives the inset the parent's scale-bar style, unit and
    length, but hidden. Ticking *Show scale bar* later matches the parent's
    style, at the inset's magnification.
  - *Replicate to…* gives each copy the source inset's bar (shown or not, same
    µm length and style), not its own parent's, so a linked group stays one
    inset repeated across pixel sizes. The bar stays per inset afterwards and
    is never propagated.
  - **Existing projects are not migrated:** insets created before this keep
    their bar. Untick *Show scale bar* in the Labels tab to remove it.
  - Consistency rule `scalebar` (row with a bar on some panels only) now
    compares ordinary panels with each other and insets with each other, so
    an inset without a bar beside its parent is no longer reported.
- Messages use proper singular / plural ("1 panel", "3 panels", "1 error, 2
  warnings") instead of "panel(s)", "inset(s)", "image(s)" or "warning(s)":
  status bar, consistency check, Lock and Replicate dialogs, undo labels,
  Home and Info tabs, export notes.
- Skipped-channel status no longer repeats the channel: "Channel 2 white:
  applied to 1 locked panel; 1 skipped (no channel 2: panel 3)".
- Inset and lock screenshots regenerated (new wording, *Show number*
  checkbox). `ARCHITECTURE.md` gives the current project format version.
- **Lock groups are named, never shown by their internal id.**
  - The Preview tab reads *🔒 Locked with N other panel(s) (split row |
    montage | condition grid | manual lock)*, and the mixed case lists kinds.
  - Consistency messages say *lock group (split row, panels 2–4)* and
    *linked inset group (panels 3–4)* instead of `lock:…` / `grid:…` /
    `region:…`.
  - New helpers: `layout.lock_kind`, `panel_numbers` and `lock_group_name`.
- **Replicate inset dialog is now a checklist.** It lists every panel of the
  figure that can receive the inset (the parent's split / lock group first,
  then figure order). The selected panels are pre-checked, otherwise the
  group, otherwise none. **All / None / Same image / Same geometry** quick
  checks are added, and the button reads **Replicate to N panels** (disabled
  at 0). `replicate_prompt(src, sets)` is unchanged, and `sets` gains
  "Eligible panels".
- **Replicate to…** is enabled whenever exactly one inset is selected, or a
  parent with exactly one inset. When it is greyed out, its tooltip says why
  (*Select an inset first*, *Select only one inset to replicate*, *This panel
  has N insets…*, *No other panel in the figure to replicate to*).
- **Default mode:** Same region only if every checked target is the same
  image, or the same geometry at the same stage position; otherwise Same
  size. It is recomputed when the checks change, until the user clicks a
  mode. The warning text is unchanged.
- **Placement:** when the source inset sits below its parent, or the parents
  form a row (split channels, a lock group in a row), each copy goes directly
  below its own parent, with the same vertical gap and left alignment.
  Otherwise the offset-then-free-place rule still applies. *Create inset* on
  a parent that is part of a row places the inset below it when there is room.
- Inspector group line: *Linked group: N insets, same region* (or *same size*
  / *mixed*). The internal group id is no longer shown.
- Status bar after *Create inset*: "Inset created — use Replicate to… to
  repeat it on other panels".
- `scripts/make_inset_screenshots.py` forces `QLocale(English, UnitedStates)`
  so screenshots show "2.0 mm". The screenshots and *Insets, step by step*
  are updated to the checklist flow and the new default rule.
- `twine` added to the `dev` extra (`uv.lock` updated).
- Project format version 3: `InsetStyle.region_group` / `region_link`
  (older projects are migrated on load, logged as `project.migrate`).
- Create inset can be started with several panels selected (drawn on the
  panel pressed).

## [0.2.0] - 2026-11-19

Figure editor, steps 1 to 6 (docs/design/figure-editor.md §6), and project
archives.

### Added (archives)
- `.pcarchive` project archives (zip, format_version 1; PysternBlot's
  `.pbarchive` system adapted): manifest with the SHA-256 and size of every
  member, the SHA-256 of the project state, app version, creation time,
  level and log-chain status.
- Light level: per image, the planes and region the figures show (plus a
  32 px margin) as uncompressed OME-TIFF with calibration, original metadata
  and the source file's SHA-256; restored images read these extracts with the
  original geometry (`ImageRef.extract`, `readers/extract.py`).
- Complete level: every original raw file, byte-exact, stream-copied and
  SHA-256 verified after copy.
- Sources verified before archiving (missing refused; modified refused unless
  allowed, then logged); optional exports + sidecars and integrity report.
- Import as a new project, archive treated as untrusted: safe member names,
  no symlinks or duplicates, manifest/member agreement, size, total and
  compression-ratio limits (binary 1000:1; JSON/HTML/SVG 200:1 and 64 MB),
  free disk space (members + 5 %) in the staging and workspace locations,
  every SHA-256 and the project-state hash verified in a staging directory
  before anything is written; a failure leaves nothing.
- Images restored from a light archive are reported as such everywhere: the
  export sidecar (`extract` block, `light_extract` flag, extract SHA-256
  verified, original SHA-256 recorded but never shown as verified), the PDF
  info page, the source-data sidecar, the integrity report (JSON + HTML) and
  an info-level consistency item.
- Home tab: Export archive… / Import archive…, with cancellable progress;
  files are streamed, never read whole into memory.
- Integrity report: archives section (level, manifest hash, members verified,
  archive file re-checked).

### Fixed
- Exporting without a running Qt application (scripts) crashed the
  interpreter; a QApplication is now created when needed.

### Added (step 6: consistency check, export, provenance)
- Consistency check (toolbar, and automatically before export): per row,
  column and lock group — same channel shown with different levels / LUT /
  gamma / invert, different T / Z / projection in a row (montages exempt),
  scale bar on some panels of a row only, channel name / wavelength
  mismatches; per panel — non-linear gamma, effective resolution below the
  preset dpi, lines thinner than the preset minimum, saturated pixels in the
  crop, content past the page edge, scale bar longer than the panel,
  unresolved label tokens; per source — missing / modified / changed / hash
  not recorded. Clicking a warning selects its panels; "Export anyway" is
  logged with the warnings.
- Export to PDF and SVG (text as text, vector lines and shapes, panel rasters
  at native crop resolution; PDF images lossless, fonts embedded, multipage,
  optional provenance info page) and TIFF / PNG (chosen dpi, RGB 8-bit,
  nearest-neighbour or smooth resampling, one file per page).
- Source-data export: raw cropped planes (visible channels or the max
  projection) as uncompressed OME-TIFF with calibration.
- Provenance sidecar `<file>.provenance.json` for every export; its SHA-256
  embedded in the files (SVG metadata, PDF info + XMP, TIFF description, PNG
  text); sources re-verified by SHA-256 before export (missing / modified
  refused unless allowed); one log entry per export with the SHA-256 of every
  written file and of the sidecar.
- Integrity report: HTML version (PysternBlot style) next to the JSON, and an
  exports section re-checking every exported file.

### Changed (step 6)
- Canvas and export share one scene builder (`FigureScene.populate`,
  `ui/scene_builder.py`).

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
