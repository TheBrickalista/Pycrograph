# Figures

The Figures tab is the figure editor: a page at journal width (millimetres),
panels rendered from the raw files, and an inspector that edits **all selected
panels** at once.

## Toolbar

- Figure selector, **New Figure…**, **Rename…**, **Add Images…**, **Delete**,
  **Undo / Redo** (every gesture is one undo step), **Fit**, zoom,
  **Outlines ▾** (see Canvas below).
- **Check consistency**, **Export…** (PDF, SVG, TIFF, PNG),
  **Export source data…** (OME-TIFF of the raw crops).
- Layout row: **Align ▾** (edges, centres, distribute), **Grid** with its gap,
  **Size ▾** (same width / height / both), **Align magnification**,
  **Page setup…**, **Auto ▾** (split channels, montage of T or Z, condition
  grid with shared or per-image rendering).

## Adding images

**Add Images…** opens a picker showing the same tree as the Library tab:
datasets with their image counts, thumbnails, and the source-file status.

- **Search** (name, tag, key/value, channel…) and **minimum rating** filter
  the tree. Datasets with no matching image are hidden.
- **Ctrl/Cmd-click** and **Shift-click** select several images. Selecting a
  dataset (or the project) means all its visible images. The panels are
  added **in tree order**, in **one undo step**, and an image that belongs to
  several datasets is added once.
- **Double-click** an image to add just that image and close the picker.
  **Add N images** adds the selection, and is disabled when nothing is selected.
- The preview pane shows the thumbnail and metadata of the current image.
- Images whose source file is **missing** are greyed out and can't be
  selected (the tooltip gives the reason). Changed or modified files can still
  be added, and keep the Library's ⚠ / ⏳ flag.
- **Import from disk…** registers files by reference into a dataset (the
  current one is offered first), then refreshes the tree with the new images
  selected. The action is available even in a project with no images yet.
- The picker remembers its size, its collapsed datasets and the last dataset
  you used for the session.

## Canvas

Click, Ctrl/Shift-click, rubber band or Ctrl+A to select. Drag to move: the
selection moves as one block, snapping to alignment guides (edges and centres
of other panels and of the page) or to the page grid. The corner handle
resizes a panel freely (Shift keeps the aspect ratio): the **crop** grows or
shrinks at constant scale — the image is never stretched. Arrow keys nudge by
0.1 mm (Shift: 1 mm). Ctrl+C / Ctrl+V on the canvas copy and paste panels.

**Panel outlines.** Unselected panels have no outline, so the canvas looks
like the exported figure. A faint dashed outline appears:

- while the mouse is over a panel;
- on every panel when **Outlines** is on (click the button; off by default);
- on a panel whose crop extends beyond its image (you zoomed out further
  than the image), so the part showing page background is still visibly part
  of the panel. Untick **Panels with empty area** in the **Outlines ▾** menu
  to hide these too.

These options are remembered for the next session. They only change the
display: they are not saved in the project, not logged and not undoable, and
outlines are never exported. The selection frame, resize handle, alignment
guides and inset rectangles are unchanged.

## Inspector

- **Preview** — lock (shared levels / LUTs, see {doc}`locks`), viewport (mini-view with the crop
  rectangle; drag to pan, wheel to zoom; zoom %; Fit), rotation and flips,
  T / Z and a max-intensity Z projection, channel controls matched by channel
  index (values that differ between the selected panels show as mixed),
  copy / paste rendering and viewport, reset to / save as image defaults,
  replace image.
- **Labels** — labels with tokens (`[channels]`, `[time]`, `[time:min]`, `[z]`,
  `[z:um]`, `[zoom]`, `[image]`, `[dataset]`, `[tags]`, `[kv:Key]`; values not
  found show as "?" and are listed), ten positions, font and colour; scale bar
  (length, unit, corner, colour, thickness, text, above / below); border drawn
  inside the panel edge.
- **Insets & Shapes** — insets (create, replicate, linked groups: see
  {doc}`insets`), inset style and follow flags; arrows, lines, rectangles and
  ellipses stored in image pixels.
- **Info** — source, crop, zoom, effective resolution (flagged below the
  page dpi), pixel scale.
- **Checks** — the figure's consistency issues, kept up to date while you
  edit (see below).

## Checks

The **Checks** tab lists what may be wrong with the figure, grouped in three
levels. Each level has its icon and its name, never only a colour:

- **⛔ Critical**: the figure is wrong or incomplete as exported — a source
  missing or modified, an image not in the project, a label value printed as
  "?", content cut off at the page edge, two different stains on one locked
  channel, or levels that differ inside a lock group (a broken lock).
- **⚠ Major**: likely to mislead — the same stain shown with different levels
  on panels of a row or column that are not locked together, a lock group
  mixing bit depths, T / Z differing in a row, saturated pixels, resolution
  below the page dpi on a panel, a scale bar longer than its panel (or not drawable),
  scale bars on some panels of a row when the others are at another scale, a
  linked inset group out of step, a source changed on disk, page or text
  size outside the journal preset.
- **ℹ Minor**: worth a look or a word in the legend — thin lines, one scale
  bar for a row of panels at the same scale (fine if the legend says so), an
  inset below the page dpi (see below),
  one stain from different lock groups in a row or column (intended?),
  gamma ≠ 1, a light-archive extract, a SHA-256 not recorded yet.

Rows and columns compare each **stain** with itself (channel name and
wavelength): a row of single-channel files of different wavelengths (`_w1`,
`_w2`…) is never compared across stains. An inset that follows its parent's
rendering is compared through its parent, so its parent's issue is not
reported twice.

**Why a low-resolution inset is only Minor.** An inset exists to show a
region enlarged, so its pixels are often fewer than the page dpi asks for
(for example 250 ppi on a 300 dpi page: enlarged ×1.2). Exported with
nearest-neighbour resampling (the default), each image pixel becomes a block
of output pixels: the file still has the journal's dpi and no value is
invented. On an ordinary panel the same thing means the image is printed
larger than its data supports, so it stays **Major**. With *Smooth*
resampling in a TIFF / PNG export, the enlarged inset pixels are
interpolated (values not in the raw data): the export dialog and the
provenance sidecar say so. PDF and SVG embed every panel at its native
resolution and are not concerned.

The check runs half a second after each change (*Check while editing*; the
status bar shows the counts). Saturated pixels need to read every crop:
**Full check** (or *Check consistency* in the toolbar) adds them. Click an
issue to select its panels.

## Export

**Export…** first runs the full check. Missing or modified sources refuse
the export. Any other critical issue needs the *Export anyway despite …
critical issues* tick; major and minor issues only need **Export anyway**.
The issues exported despite are recorded in the log and the provenance
sidecar, with their level. PDF and SVG keep text as text and embed
each panel's pixels at native resolution (PDF lossless); TIFF / PNG are
rendered at a chosen dpi with nearest-neighbour (default) or smooth
resampling. See {doc}`../concepts/provenance`.
