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

## Missing or moved files

A missing file shows ⚠. *Relink…* accepts a new location only if the file's
SHA-256 matches the recorded one, and offers the same folder change for every
other missing image (e.g. a drive remounted as `/Volumes/LaCie 1`).
