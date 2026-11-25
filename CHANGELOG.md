# Changelog

All notable changes to Pycrograph are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2] - 2026-11-25

Third beta. Page setup draws on a sourced catalog of journal page presets
(57 presets from 20 publishers, slide sizes included): pick the publisher,
then the size, and see where each value comes from; page and text size are
checked against the journal. The consistency check is reworked: rows and
columns compare each stain with itself (no more false alarms on
one-file-per-wavelength figures), issues come in three levels (critical,
major, minor), and they are kept up to date in a live Checks panel. Export
asks for an explicit tick when a critical issue remains.

### Compatibility
- Project format unchanged: 5, as in 0.2.1 (0.2.0 cannot open it).
- Figure provenance sidecar `report_version` 2: overridden issues give
  `level` and `rule` instead of `severity`; `code` values are unchanged.
  Sidecars and log entries written by 0.2.x stay valid: the integrity report
  still verifies those exports by their SHA-256 and counts their issues.
- Figures made with the old 8 pt Nature default now get **font_size**
  issues (8 pt is above Nature's 5–7 pt text range). To clear them, pick
  the size again in *Page setup* (the figure then takes the catalog's
  values) or set the labels' font size.
- New figures default to the catalog's *Nature double column*: 7 pt text
  and 0.25 pt minimum lines (was 8 pt and 0.5 pt).

### Added
- **Journal page presets catalog** (`resources/journal_presets.json`, 57
  presets from 20 publishers, validated at load). It is the source of the
  built-in presets; publisher, status (official / secondary / mixed), fields
  the journal does not state, font range, journals, source and note stay in
  the catalog (`journal_presets.JournalPreset`), looked up by preset name.
- **Page setup**: choose **Publisher ▾** then **Size ▾** (e.g. *single column
  — 89 mm*), or **Custom** (your presets, or a free page size). The dialog
  shows the preset's sizes, dpi, font, minimum line, journals, note, source
  link and a status badge (*Official*, *Secondary — verify*, *Mixed*);
  values the journal does not state are greyed as defaults.
- Consistency check: **page_size** (page wider than the preset width or
  taller than its maximum height) and **font_size** (label or scale-bar text
  outside the journal's range, when given; one-letter panel letters are
  checked against the minimum only). Warnings say when the limit is a default
  rather than a journal rule.
- **Presentation slides** presets (PowerPoint widescreen 16:9 and standard
  4:3, Google Slides 16:9, 16:10): they open as wide pages, in landscape
  (`PageSetup.preset_layout`: a preset at least 1.25 times wider than tall);
  the orientation toggle turns the same paper. Journal presets still open
  in portrait.
- Guide page *Page setup and journal presets*, with the preset table
  generated from the catalog (`scripts/make_journal_presets_table.py`).
- `--selfcheck` validates the bundled catalog.
- **Checks panel** (Figures tab, *Checks*): the consistency issues of the
  figure grouped by level with counts, re-checked 500 ms after each
  committed gesture (no saturation; **Full check** adds it); counts in the
  status bar; click an issue to select its panels. *Check consistency* opens
  it instead of a modal dialog.

### Changed
- **Consistency levels**: *critical* / *major* / *minor* replace error /
  warning / info, decided in one table (`consistency.RULES`), always shown
  with an icon and a name. Export still refuses missing / modified sources;
  any other critical issue needs an explicit *Export anyway despite …* tick.
- Low effective resolution on an **inset** is *Minor* (rule
  `resolution.inset`: "pixels shown enlarged ×1.2 … fine for an inset,
  exported nearest-neighbour"); on an ordinary panel it stays *Major*
  (`resolution.panel`; code `resolution` unchanged). A TIFF / PNG export
  with smooth resampling and such an inset says, in the export dialog and
  the sidecar warnings, that the enlarged pixels are interpolated (not
  blocking; PDF / SVG not concerned).
- Figure provenance sidecar `report_version` 2: overridden issues give
  `level` and `rule` instead of `severity` (`code` values unchanged; the
  source-data sidecar stays at 1).
- Built-in presets always come from the catalog: `preferences.json` stores
  only the user's own presets, and a saved preset with a built-in name is
  replaced by the catalog's. The Preferences tab shows built-ins read-only
  (Duplicate to adapt one; *Restore built-in presets* is gone) and chooses
  the default preset by publisher, then size.
- New figures get the catalog's *Nature double column*: 7 pt text (was 8 pt)
  and 0.25 pt minimum line (was 0.5 pt). Existing figures keep their stored
  preset; no project format change.
- Existing figures set up with the old 8 pt Nature default now get
  **font_size** issues (Major): 8 pt is above Nature's 5–7 pt text range (panel
  letters are exempt from the maximum). Pick the size again in Page setup,
  or set the labels' size, to clear them.

### Fixed
- Consistency check false positives: rows and columns compared channels by
  index, so a row of single-channel files of different wavelengths (`_w1`,
  `_w2`…) reported *levels* and *channels* issues; stains are now compared
  by name / wavelength. One stain from different lock groups in a row or
  column is one Minor issue per row / column; a scale bar on one panel of a
  same-scale row (µm per page-mm within 1 %) is Minor, "fine if stated in
  the legend"; levels a lock group holds clamped per bit depth are reported
  under *bit_depth*, not as *levels*; insets that follow their parent's
  rendering no longer repeat the parent's issue.

## [0.2.1] - 2026-11-23

Second beta. A TIFF whose planes lie along one generic axis is now read as a
Z-stack or a time-lapse from its per-plane z-positions, with a logged
"Planes are: Time / Z" override in the Library viewer; existing images keep
the mapping they were imported with. Docs and UI text describe the supported
formats generically. Projects are saved in format 5 (migrated automatically
on opening): **a project opened or saved by 0.2.1 can no longer be opened by
0.2.0.**

### Added
- **Time-lapse or Z-stack for TIFF planes.** A TIFF whose planes lie along
  one generic axis (tifffile `I`, e.g. a multi-page TIFF with one page per
  plane) is read as a **Z-stack** when the file records per-plane
  z-positions (currently MetaMorph) and they form a regular series: at
  least 3 planes, strictly monotonic, step spread ≤ 10 %. The Z
  step comes from those positions. Otherwise it is read as a time-lapse, as
  before (constant z, or autofocus drift). The decision is recorded in
  `metadata.raw["tiff.axis_inference"]`, e.g. "I→Z (MetaMorph z-position,
  step 0.5 µm)".
- **Planes are: Time / Z** selector in the Library viewer for such images
  (`storage.set_plane_axis`, `ImageRef.plane_axis_override`). It re-reads the
  metadata, resets the library position to the first plane and logs
  `image.set` (field `plane_axis`). The raw file and its SHA-256 are untouched.
  It is refused while a figure panel or inset uses the image (the error
  lists the figures; remapping panels' T / Z is not supported), and for single
  planes, files with their own T / Z axes, light-archive extracts and files
  changed since import.
- The Library info pane, the integrity report (JSON and HTML) and the
  provenance sidecar record the plane-axis mapping (`plane_axis`: axis,
  override, inference).

### Changed
- README badges, Zenodo DOI in CITATION.cff, manual CodeQL trigger.
- Docs and UI text describe the supported formats generically (TIFF, ImageJ,
  MetaMorph); MetaMorph is no longer presented as the only input.
- Project format **5**: `ImageRef.plane_axis_override` (the migration writes
  `null`, logged as `project.migrate`).
- TIFF reader revision 2 (`reader_version` "pycrograph … (tiff reader 2)").
  **Existing images are not re-interpreted:** files are reopened with the
  mapping recorded in the project (images imported before keep T) until the
  user changes it.

## [0.2.0] - 2026-11-20

First public beta. Figure editor (docs/design/figure-editor.md §6, steps 1 to 6),
lock groups, replicated insets, project archives, the Home and Projects tabs,
how-to guides, and the signed release pipeline.

### Added

#### Pages and panels (editor step 1)

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

#### Preview (step 2)

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

#### Layout (step 3)

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

#### Labels, scale bar and border (step 4)

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

#### Lock groups

- Lock propagation: an edit of a channel's LUT, black, white, gamma or invert
  on a locked panel applies to the same channel of every panel of its group
  (clamped per image, panels lacking the channel skipped), in one gesture.
  Visibility, T/Z, projection, crop, orientation and labels are never shared.
- Preview tab: lock indicator, Lock selection (syncs to the first panel),
  Unlock, Select group.
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
- Provenance sidecar: per panel `lock_kind` (*split row*, *montage*,
  *condition grid*, *manual lock*) next to `lock_group`.

#### Insets and shapes (step 5)

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

#### Consistency check, export and provenance (step 6)

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

#### Project archives

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

#### Home tab and adding images

- **New Home tab** (`ui/home_tab.py`, laid out like PysternBlot's), where the
  app now opens.
  - It shows the logo (sharp on HiDPI screens, on a light card with a dark
    palette), "Version x.y.z" under it, a one-line description, and the
    copyright and GPLv3 notice.
  - Big buttons: **New Project**, **Open Project** (→ Projects tab),
    **Import Images** (the toolbar's import flow, then the Library tab),
    **Figures** (→ Figures tab), **Import Archive…**, and **About / License**.
    *Import Images* and *Figures* are greyed out, with a tooltip, until a
    project is open.
  - New guide page `guide/home.md`.
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

#### About tab, licence and third-party notices

- **About tab like PysternBlot's**: **Legal**, **Copyright & Credits** and
  **Repository** buttons over a read-only text view, Legal first.
  - Legal: the bundled `LICENSE.txt`, with a gnu.org link if it is missing.
  - Copyright & Credits: the copyright line, the GPLv3 statement, the bundled
    libraries with their licences, then the full third-party notices.
  - Repository: version, source and issues URLs, and the citation built from
    the bundled `CITATION.cff` (authors, year, version). Its DOI line gives the
    concept DOI once `CITATION.cff` has a `doi:`; until then it reads "DOI:
    assigned by Zenodo at the first release".
- `pycrograph/resources/LICENSE.txt` and `CITATION.cff`: tracked copies of the
  repository files; a test checks they are identical.
- `pycrograph/resources/THIRD_PARTY_NOTICES.txt`, generated by
  `scripts/make_third_party_notices.py` from the locked environment. It covers
  the runtime closure of `uv.lock` (PySide6 / shiboken6 / Qt under LGPL-3.0,
  with the dynamic-linking, replacement and source note; NumPy; Pillow;
  Pydantic and pydantic-core; tifffile; annotated-types; typing-extensions;
  typing-inspection) and the embedded Python runtime. Each entry has its
  version, copyright line and full licence text. Both release builds
  regenerate it before PyInstaller; a test checks every runtime dependency in
  `uv.lock` is covered.
- `--selfcheck` checks that the licence, the notices and `CITATION.cff` are
  bundled.

#### Documentation

- Docs: how-to page **Insets, step by step** (`docs/guide/insets.md`). It
  covers creating insets (on one panel or several), replicating them (table
  columns, Same region vs Same size, the registration warning, Keep linked),
  three worked cases (split channels, MetaMorph files per wavelength, other
  cells or objectives), working with a linked group, and troubleshooting.
  It is linked from the Figures guide and from *Locks and insets*.
- `scripts/make_inset_screenshots.py`: regenerates the guide's screenshots
  (`docs/_static/img/insets-*.png`) offscreen from synthetic images, with a
  smoke test so it keeps working when the UI changes.
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
- Locks guide: case (b) shows the bit-depth warning (new screenshot
  `locks-depth.png`) and the check; troubleshooting shows the skipped-panel
  message.

#### Release pipeline and tooling

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
- Dev dependency `pytest-qt` (≥ 4.5.0, `qt_api = "pyside6"`); UI tests for the
  picker.
- `pycrograph/ui_text.py`: `plural(n, word)` / `noun(n, word)` for
  user-facing wording, with a test guarding against `(s)` in message strings.

### Changed

#### Figure editor

- **Figures canvas: panel outlines are optional.** Unselected panels no
  longer have a dashed outline by default. It appears:
  - while the mouse hovers the panel;
  - on every panel with the new **Outlines** toolbar button (next to
    Fit / + / −), off by default;
  - on panels whose crop extends beyond the image (page background inside
    the panel). This one is on by default; untick *Panels with empty area*
    in the button's menu to hide it.

  Both options are application preferences (`preferences.json`), remembered
  across sessions. They are not project data, not logged and not undoable.
  Exports never show outlines. Selection, handles, guides and inset
  rectangles are unchanged.
- Canvas and export share one scene builder (`FigureScene.populate`,
  `ui/scene_builder.py`).
- Multi-panel pan converts the displacement through each panel's own
  orientation (it used the first panel's).
- Figure-level log entries use leaf paths (`page.width_mm`).
- Resizing a panel extends or shrinks the crop at constant scale (crop
  centre kept) instead of scaling the image; the handle resizes freely,
  Shift keeps the aspect ratio.
- The canvas draws the image at one uniform scale, clipped to the panel;
  where the crop runs off the image the page background shows (it was
  drawn into the whole panel rectangle).
- `Crop.x` / `Crop.y` may be negative (crop past the image's top/left).
- `PROJECT_FORMAT_VERSION` is 2. Version-1 projects are migrated on load
  (`Figure.page` `PagePreset` → `PageSetup`); the migration is recorded as a
  `project.migrate` log entry and written at the next save.
- About opens the About tab instead of a dialog.

#### Labels, locks and layouts

- `[time]` without a unit uses one unit per lock group / montage, chosen from
  its largest time; single panels unchanged.
- Shared rendering (lock propagation, Lock selection, condition grid) warns
  when the same channel index has a different channel name or wavelength.
- Lock selection asks for confirmation when the panels' levels / LUTs differ.
- Rows take the first row's per-channel rendering by default (crop, T/Z and
  projection stay per image); "Condition grid (keep each image's rendering)"
  keeps them, with one lock group per row.
- **Lock groups are named, never shown by their internal id.**
  - The Preview tab reads *🔒 Locked with N other panel(s) (split row |
    montage | condition grid | manual lock)*, and the mixed case lists kinds.
  - Consistency messages say *lock group (split row, panels 2–4)* and
    *linked inset group (panels 3–4)* instead of `lock:…` / `grid:…` /
    `region:…`.
  - New helpers: `layout.lock_kind`, `panel_numbers` and `lock_group_name`.

#### Insets

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
- Project format version 3: `InsetStyle.region_group` / `region_link`
  (older projects are migrated on load, logged as `project.migrate`).
- Create inset can be started with several panels selected (drawn on the
  panel pressed).
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
- Deleting a parent also clears its insets' style (they become ordinary).

#### Home, Projects and branding

- **The former Home tab is now the Projects tab** (`ui/projects_tab.py`,
  `ProjectsTab`), with the same behaviour. Tab order: Home, Projects, Library,
  Figures, Preferences, About. The guide page `guide/home.md` became
  `guide/projects.md`.
- **The app icon is the PG mark** (`resources/pycrograph_icon.png`): window
  icon, the macOS `.icns` and the Windows `.ico` of the release builds. The
  full logo with its wordmark (`resources/pycrograph_logo.png`) is shown on
  Home. Both PNGs are rendered from `docs/_static/logo/*.svg` (QtSvg cannot
  draw the logo's clipPath / blur). `--selfcheck` checks that both are bundled.
- Docs: the PG mark is the sidebar logo and the favicon. The README opens with
  the logo.

#### Messages

- Messages use proper singular / plural ("1 panel", "3 panels", "1 error, 2
  warnings") instead of "panel(s)", "inset(s)", "image(s)" or "warning(s)":
  status bar, consistency check, Lock and Replicate dialogs, undo labels,
  Home and Info tabs, export notes.
- Skipped-channel status no longer repeats the channel: "Channel 2 white:
  applied to 1 locked panel; 1 skipped (no channel 2: panel 3)".

#### Documentation

- Inset and lock screenshots regenerated (new wording, *Show number*
  checkbox). `ARCHITECTURE.md` gives the current project format version.
- `scripts/make_inset_screenshots.py` forces `QLocale(English, UnitedStates)`
  so screenshots show "2.0 mm". The screenshots and *Insets, step by step*
  are updated to the checklist flow and the new default rule.

#### Tooling and CI

- `twine` added to the `dev` extra (`uv.lock` updated).
- CodeQL analysis is skipped on private repositories.

#### Credits and release plan

- Authors: Etienne Boulter and Chloé C. Féral (Université Côte d'Azur, CNRS,
  Inserm, IRCAN, Nice), as for PysternBlot. They appear in `CITATION.cff`
  (with ORCIDs, affiliations and a `references:` entry for PysternBlot),
  `pyproject.toml`, the docs and CONTRIBUTING. The Home and About tabs and the
  README show "Copyright © 2026 Etienne Boulter, Chloé C. Féral — Université
  Côte d'Azur, CNRS, Inserm, IRCAN" (`pycrograph/credits.py`).
- Release plan: 0.x releases are betas, published as GitHub pre-releases and
  each archived on Zenodo with a DOI. 1.0.0 will be the first stable release,
  also on PyPI. "Development Status :: 4 - Beta".

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
- Exporting without a running Qt application (scripts) crashed the
  interpreter; a QApplication is now created when needed.
- The Preview inspector could show the previous selection's widgets until
  the event loop deleted them.

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
