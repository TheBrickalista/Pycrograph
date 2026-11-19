# Figures

The Figures tab is the figure editor: a page at journal width (millimetres),
panels rendered from the raw files, and an inspector that edits **all selected
panels** at once.

## Toolbar

- Figure selector, **New Figure…**, **Rename…**, **Add Images…**, **Delete**,
  **Undo / Redo** (every gesture is one undo step), **Fit**, zoom.
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
Unselected panels have a faint dashed outline, never exported.

## Inspector

- **Preview** — lock (shared levels / LUTs, see
  {doc}`../concepts/locks-and-insets`), viewport (mini-view with the crop
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

## Export

**Export…** first runs the consistency check (levels differing within a row,
column or lock group; T/Z differing in a row; scale bars on some panels only;
channel name mismatches; sources missing or modified; low resolution; thin
lines; saturated pixels; content past the page edge; unresolved labels).
"Export anyway" records the warnings. PDF and SVG keep text as text and embed
each panel's pixels at native resolution (PDF lossless); TIFF / PNG are
rendered at a chosen dpi with nearest-neighbour (default) or smooth
resampling. See {doc}`../concepts/provenance`.
