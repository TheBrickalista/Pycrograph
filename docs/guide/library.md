# Library

The Library tab is the image browser and the place where each image's
**default display** is set (copied into a panel when the image is added to a
figure).

## Projects, datasets, images

The tree is OMERO-style: *Project › Dataset › Image*. One file can sit in
several datasets. Toolbar: **New Dataset…**, **Import Images…** (by reference),
**Add to Figure**, **Check Sources**, **Integrity Report…**.

Right-click an image: *Add to figure*, *Relink…*, *Verify file (re-hash)*,
*Copy into project*, *Add to dataset…*, *Remove from this dataset*.

## Annotations

Tags, a rating (★), key–value pairs (used by `[kv:Key]` label tokens and the
condition grid's row labels) and a stain / antibody per channel (used by the
`[channels]` token). Search filters the tree by name, tag, key / value or
channel.

## Viewer and channels

The centre view shows the composite (Shift+wheel to zoom, drag to pan,
double-click or **Fit** to fit), with T and Z sliders for stacks and a line
giving pixel size and field of view. Each channel has visibility, a LUT
(named or custom colour), a histogram with draggable Black / White gates,
typed values, *Auto* (0.1–99.9 percentiles), *Full range*, gamma, invert and a
saturation badge. Rotation (90°) and flips are display settings too.

## Time-lapse or Z-stack?

Some TIFF files do not say whether their planes are time points or Z
positions. A MetaMorph multi-page TIFF stores both the same way, one page
per plane. Pycrograph decides at import from MetaMorph's per-plane
*z-position*:

- **Z-stack** when the positions change by a regular step (at least 3 planes,
  always in the same direction, step within ±10 %). The step becomes the Z
  step, so maximum projections, Z montages and `[z:um]` labels work.
- **Time-lapse** otherwise: the same position for every plane, or small
  autofocus moves up and down.

For such files the viewer's T / Z row shows **Planes are: Time ▾ / Z ▾**, and
the info pane shows the decision, for example *Plane axis: Z (MetaMorph
z-position, step 0.5 µm)*. If it is wrong, choose the other value. Pycrograph
re-reads the file's metadata (the raw file and its SHA-256 are untouched),
goes back to the first plane, and records the change in the project log.
The integrity report and the export provenance record it too.

**Refused when the image is in a figure.** Panels and insets remember which
time point or Z plane they show (and Z projections). After a switch those
numbers would point at other planes, so Pycrograph refuses the change and
lists the figures that use the image. Remove its panels first (or switch the
axis before adding the image to a figure). The change is also refused for a
light-archive copy, whose archived planes keep their original numbering.

Images imported with an earlier version keep the axis they were imported
with (time) until you change it. Nothing is re-interpreted automatically.

## Missing or moved files

A missing file shows ⚠. *Relink…* accepts a new location only if the file's
SHA-256 matches the recorded one, and offers the same folder change for every
other missing image (e.g. a drive remounted as `/Volumes/LaCie 1`).
