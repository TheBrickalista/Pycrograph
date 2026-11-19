# Pycrograph — Figure editor design (OMERO.figure-style)

*Design spec for sessions 2–6. Status: proposal, 2026-11-18. Read with `docs/ARCHITECTURE.md`.*

## 1. What OMERO.figure does (reference behaviour)

**Core idea.** Every panel on the page is an independent multi-dimensional viewer of one raw
image: it has its own rendering (channels on/off, colour/LUT, levels), Z/T position (or Z
projection range), zoom/pan viewport, rotation/flip, labels, scale bar and shapes. Cropping is
done by zooming/panning the viewport, so the full image is never lost. Select several panels
and every control edits all of them at once, which is how rows and columns are kept consistent.

**Window.**
- *Top toolbar / menus*: File (New, Open, Save, Paper Setup: size, orientation, number of
  pages, page colour; import/export figure JSON), Edit (undo/redo, copy/paste panels),
  Add Image, Delete, align left/right/top/bottom, snap to grid (choose the gap),
  Align Width / Align Height / Width & Height, **Align Magnification** (resize selected panels
  so scale bars of equal length look equal), export PDF / TIFF (with or without the image
  files), figure legend (goes onto the PDF info page).
- *Canvas*: pages at true paper size; drag to move, handles to resize; alignment guides while
  dragging; rubber-band and Ctrl-A selection; copy/paste of panels to build rows/columns.
- *Right panel, three tabs*:
  - **Preview**: viewport with Zoom slider (or typed zoom %) and drag-to-pan; rotation; flip
    H/V; channel toggles with colour/LUT picker and levels sliders; Z and T sliders,
    including a Z range for max-intensity projection; Copy / Paste of rendering settings and
    of the viewport (crop) between panels; crop to a rectangle drawn on the image or to an ROI.
  - **Labels**: *Add labels* (free text or a source picked from a dropdown: channels, time
    with chosen units and offset, Z with offset, zoom, image/dataset name, tags, key–value
    pairs); size, colour, position (top, bottom, left, right, left/right vertical, inside
    corners); *Edit labels* list. *Scalebar*: length, unit, position, colour, show/hide its
    text label, font size. *ROIs/shapes*: edit (rectangle, line, arrow, ellipse, polygon),
    copy/paste between panels, colour/stroke width (stroke in page points so all panels match),
    **Create inset**. *Panel border* (colour, width).
  - **Info**: image name, dimensions, pixel size, channel names (renameable for labels),
    panel x/y/width/height, export dpi, link to source, **Edit ID** to swap the image in the
    selected panels while keeping all their settings (checks that the new image is
    compatible).
- **Dynamic insets** (2024–25): *Create inset* makes a new panel showing a region of the
  parent and draws that region as a rectangle on the parent. The two stay synchronised: pan,
  zoom or rotate the inset and the rectangle follows; edit the rectangle and the inset follows.
  Optional matching border on the inset.
- **Undo/redo of everything**, multi-page figures, figure stored as one JSON document.

## 2. Mapping onto Pycrograph

### 2.1 Top-level tabs (PysternBlot style `QTabWidget`)

| Tab | Content |
|---|---|
| **Home** | Project list (new/open/archive), recent figures |
| **Library** | What exists now: tree + single-image viewer + channel inspector. Settings made here are the image's *defaults*, copied into a panel when it is created |
| **Figures** | The editor below (one sub-tab or a dropdown per figure in the project) |
| **Preferences** | Journal presets (data, editable), default fonts, label defaults |
| **About** | Version, licence, citation |

Later: a **Provenance** view per figure (panel → source file, hash, settings, log), as in
PysternBlot's "Original Image" tab.

### 2.2 Figures tab layout

```
┌ toolbar: Add ▾ · Delete · Undo/Redo · Align ▾ · Grid ▾ · Size ▾ · Align magnification ·
│          Page setup · Zoom (canvas) · Check consistency · Export ▾
├──────────────┬──────────────────────────────────┬────────────────────────────────┐
│ Library dock │ Canvas (pages in mm, grid,       │ Inspector tabs:                │
│ (tree +      │ rulers, guides, selection)       │  Preview · Labels · Insets &   │
│ thumbnails;  │                                  │  Shapes · Info                 │
│ drag images  │                                  │                                │
│ onto page)   │                                  │                                │
└──────────────┴──────────────────────────────────┴────────────────────────────────┘
```

Inspector tabs (same split as OMERO, with shapes moved out of Labels for room):

- **Preview**: viewport mini-view (drag = pan, wheel / slider / typed % = zoom, shows the
  crop rectangle on the full image); 90° rotation + flips; per-channel controls (reuse
  `ui/channel_controls.py` unchanged); T and Z sliders; Z-projection range (max); Copy/Paste
  rendering, Copy/Paste viewport; **Reset to image defaults** / **Save as image defaults**.
- **Labels**: add label (text with tokens, §2.5), list of labels; scale bar; panel border.
- **Insets & Shapes**: Create inset; inset rectangle colour/width; shapes (arrow, line,
  rectangle, ellipse) in image coordinates; copy/paste shapes.
- **Info**: source file, SHA-256 status, reader, dimensions, calibration, channel names and
  stains; panel x/y/w/h in mm; **effective resolution** (§2.6); **Replace image…** (keeps
  everything; refuses incompatible channel count / missing calibration for a scale bar).

### 2.3 Multi-selection semantics (the key OMERO behaviour)

- Every inspector control applies to **all selected panels**. A control whose value differs
  between them shows a mixed state (blank field, partially checked box, slider handle hidden);
  touching it sets all of them.
- Channel controls are matched by **channel index**; panels whose image lacks that channel are
  skipped and the status bar says how many were.
- Levels range adapts to the largest bit depth among selected images; values are clamped per
  image.
- One gesture on N panels = one undo step and one log entry **per panel** (target_id = panel
  id), so the provenance of each panel stays complete.

### 2.4 Viewport = crop (single source of truth)

OMERO stores zoom % + pan offsets. Pycrograph stores the **crop rectangle in raw-pixel
coordinates** (`Panel.crop`, already in the model), with the panel's aspect ratio. Zoom % and
pan are derived values in the UI. Why: provenance reads directly as "pixels x 120–580,
y 64–424 of file …", and export can cut the raw array without any interpolation.

- Resizing a panel on the page keeps the crop centre and the scale (µm per mm), extending
  the crop; if the crop would leave the image, the visible area is clamped and the outside is
  drawn as page background (never padded into the pixels).
- **Rotation**: only 0/90/180/270 + flips in v1 (pure index operations, no resampling).
  OMERO allows free angles; if we add them later they are flagged in provenance as a
  resampling step (like PysternBlot's integrity report does for non-linear gamma).

### 2.5 Labels and tokens

Label text may contain tokens, resolved at render time from metadata:

| Token | Source |
|---|---|
| `[channels]` | visible channels' names (stain if set, else illumination name), each coloured like its channel |
| `[time]`, `[time:min]`, `[time:s]`, `[time:hh:mm]` | `ImageMetadata.time_offsets_s[t]`, minus an optional per-figure offset (t = 0 at drug addition, etc.) |
| `[z]`, `[z:um]` | Z index or position × `calibration.z_step`, optional offset |
| `[zoom]` | magnification relative to the full image |
| `[image]`, `[dataset]` | file name / dataset name |
| `[tags]`, `[kv:Cell line]` | ImageRef tags / key–value value |

Positions: top, bottom, left, right, left-vertical, right-vertical, and inside
top-left/top-right/bottom-left/bottom-right. Font family and size default to the page preset
(Arial 8 pt for Nature). "Labels from key–value pairs" = add `[kv:<key>]` to every selected
panel in one click (OMERO's most-used shortcut for condition labels on a row).

### 2.6 Scale bar, magnification, resolution

- Scale bar length in µm (calibration units), drawn at
  `bar_mm = length / (crop_w_px · pixel_size) · panel_w_mm`; updates live with zoom/resize.
  Options: position (4 corners), colour, thickness (pt), show text, font size.
  Unavailable (greyed, with the reason) for uncalibrated images.
- **Align magnification**: resize selected panels so `µm per page-mm` equals that of the
  top-left selected panel → identical scale bars look identical. (Implemented by changing the
  panel size, not the crop.)
- **Effective resolution** (our addition): `crop_w_px / (panel_w_mm / 25.4)` ppi, shown in
  Info and on the canvas when below the preset's dpi (default 300): that panel would be
  upsampled at export. This is what journals check and OMERO does not show.

### 2.7 Insets

- *Create inset* on a selected panel: draw a rectangle on its preview → new `Panel` with
  `inset_of = parent.id`, its `crop` = that rectangle, placed to the right of the parent at the
  parent's magnification × 2 (default; editable).
- **Single source of truth**: the rectangle drawn on the parent is *derived* from the child's
  crop at render time; it is not stored on the parent. So edit-either-side sync is free:
  editing the rectangle on the parent writes the child's crop, panning the child moves the
  rectangle. Also synced: the child inherits the parent's T/Z and channel settings unless
  "unlinked" (a flag), so a time-lapse inset follows the parent's time point.
- Inset style: `InsetStyle(color, width_pt, show_on_parent, border_on_inset)`.
- Several insets per parent are allowed; rectangles are numbered/coloured to match borders.

### 2.8 Layout tools

Align left/right/top/bottom/centres; distribute; **snap to grid** (rows/columns from current
positions, gap in mm, first panel fixed); same width / height / both; align magnification;
nudge with arrow keys (0.1 mm, Shift = 1 mm); alignment guides while dragging; page grid
(e.g. 1 mm) with snap toggle. Auto-layouts as shortcuts:
- **Split channels**: from one image, one panel per channel + merge, in a row
  (handoff's "auto-split").
- **Montage**: chosen T points or Z slices in a row/column.
- **Condition grid**: one row per selected image (or per dataset), one column per channel
  configuration, with `[kv:…]` row labels and `[channels]` column labels.

### 2.9 Consistency check (our addition, before export)

Within each row and each column of the grid (and within any lock group), warn when panels that
show the *same channel* differ in black/white, gamma or LUT; when Z/T or projection differs
in a row meant to compare conditions; when a source hash changed or is missing; when effective
resolution < preset dpi; when a scale bar is shown on some panels of a row but not others.
Each warning selects the offending panels on click.

## 3. Model changes (`pycrograph/models.py`)

Already present: `Figure`, `Panel` (crop, channels, t, z, `z_projection`, rotation, flips,
labels, `scale_bar`, `lock_group`, `inset_of`), `PagePreset`, `Overlay`.

To add / change:

```text
class PageSetup(BaseModel):          # replaces Figure.page: PagePreset
    preset: PagePreset               # journal preset (data)
    width_mm: float; height_mm: float
    orientation: Literal["portrait", "landscape"] = "portrait"
    n_pages: int = 1
    background: str = "#FFFFFF"
    grid_mm: float = 1.0; snap: bool = True

class PanelBorder(BaseModel): visible: bool = False; color: str = "#FFFFFF"; width_pt: float = 0.5
class InsetStyle(BaseModel):  color: str = "#FFFFFF"; width_pt: float = 0.75
                              show_on_parent: bool = True; follow_parent_tz: bool = True
                              follow_parent_rendering: bool = True

class Panel(...):                    # additions
    page: int = 0
    border: PanelBorder
    inset_style: InsetStyle | None = None     # set when inset_of is set
    time_offset_s: float = 0.0                # for [time] labels
    z_offset: float = 0.0
    shapes: list[Shape]                       # image-pixel coordinates (follow crop/zoom)

PanelLabel.position: add "left-vertical", "right-vertical"; text supports tokens (§2.5).
ScaleBar: add font_size_pt, label position ("below" | "above").
Figure: legend: str = ""   # goes to the info page
```

Bump `PROJECT_FORMAT_VERSION` to 2 with a migration (`Figure.page` → `PageSetup`).

## 4. Rendering and export

- One `PanelRenderer` used by canvas *and* export: raw plane(s) (or max projection over the
  Z range) → **crop by array slicing** (memmap: only touched rows are read) → orientation →
  `display.render_composite`. Canvas draws it scaled by Qt; export embeds the cropped RGB at
  **native resolution** in SVG/PDF (no resampling), and resamples only for TIFF/PNG at a
  chosen dpi (method recorded).
- Cache per panel keyed by (image sha, t, z-range, crop, channel settings) to keep the canvas
  responsive; max projections cached separately (they read N planes).
- Saturation badge per panel is computed on the **crop region** (PysternBlot does the same for
  its crop).
- Labels, scale bars, borders, inset rectangles and shapes are vector items in a
  `QGraphicsScene` in mm; export goes through `QSvgGenerator` / `QPdfWriter` so text stays text.

## 5. Undo / redo and log

- Undo stack = snapshots of the figure (`Figure.model_dump()`), pushed once per gesture
  (same gesture boundaries as `GestureCommitter`). Figures are small (KBs), so snapshots are
  simpler and safer than inverse commands.
- Every gesture also writes operation-log entries (one per panel affected), e.g.
  `figure.panel.set field=channels[1].white`, `figure.layout.align_left`, `figure.inset.create`.
  Undo itself is logged as an operation (the log is append-only).

## 6. Implementation order

1. **Tabs refactor + models v2 + read-only canvas**: Home/Library/Figures/Preferences/About;
   `PageSetup`, page rendered at journal width; panels drawn with `PanelRenderer`; select,
   move, resize, multi-select, delete, undo/redo; "Add to figure" from the library.
   *Done.* Resize keeps the image aspect ratio and leaves the crop unchanged (scales the
   image); the §2.4 behaviour (resize extends the crop at constant µm/mm) comes with the
   viewport controls in step 2. §7.2 taken as snapshot at creation; §7.4 as a dropdown.
2. **Preview tab**: channel controls for the selection (mixed states), T/Z + projection,
   viewport pan/zoom (crop), rotation/flips, copy/paste rendering and viewport,
   reset/save image defaults, replace image.
   *Done.* Rotation keeps the panel on the page and turns the crop about its centre at
   the same scale. Copy viewport carries crop centre, scale (px per page-mm) and
   orientation, so it pastes onto panels of another size. "Save as image defaults"
   changes the library image (logged on the image) and is not part of the figure's undo.
3. **Layout tools**: align, distribute, snap to grid, same size, align magnification, page
   setup + journal presets, split channels / montage / condition grid.
   *Done.* Same width / height scale the panel with its crop (like align magnification);
   same width & height scales to the width then fits the height at that scale (crop
   extended or shrunk). One `lock_group` per auto-layout: locks match by channel index, so
   one group covers the rows and columns of a condition grid (propagation of locked edits
   and the consistency check come later). Row/column labels of the condition grid come with
   labels (step 4). Presets are app preferences, not project data (not logged); applying one
   to a figure is logged.
4. **Labels, scale bar, border** with tokens.
   *Done.* Labels tab: a row = one (text, position) across the selected panels; tokens
   resolved at render (`labels.py`), unresolved ones drawn as "?" and reported (status bar,
   Labels tab). `[time]` picks s / min / h by magnitude; `[z:um]` uses `z_offset` in µm;
   `[image]` is the file name without extension; `[channels]` joins with " / " and shows
   grey/white LUT channels in the label colour. Outside labels stack outward, first label
   nearest the panel; label colour "auto" = black outside, white inside. Scale bar and
   border apply to all selected panels (scale bar skipped on uncalibrated images, greyed when
   none is calibrated); the border is drawn inside the panel edge. Overlays are vector child
   items tagged by kind (`ui/panel_overlays.py`) for the export. Panel copy/paste
   (Ctrl+C / Ctrl+V on the canvas) keeps labels; pasted panels are not locked. The condition
   grid adds `[kv:…]` / `[image]` row labels and `[channels]` column labels and reserves room
   for them.
5. **Insets & shapes** (dynamic insets).
   *Done.* Create inset = draw a rectangle on the selected panel (canvas draw mode); the
   inset copies the parent's rendering, T/Z and orientation (not labels, shapes or lock
   group), shows the region at 2× magnification and is placed right of the parent, moved
   down past other panels, kept on the page, snapped. The rectangle on the parent is derived
   from the inset's crop (`insets.rect_on_parent`); with the parent selected it can be
   dragged or resized by its corner (aspect kept: the inset panel keeps its page size, its
   crop and magnification change). Insets are numbered per parent in figure order; colours
   cycle through yellow, cyan, magenta, green, orange, white. The follow flags sync the inset
   inside the parent's gesture (one log entry per inset). Shapes: two raw-pixel points;
   stroke and arrowhead in page points; clipped to the panel; selected by clicking once the
   panel is selected. `[time]` in a lock group / montage uses one unit chosen from the
   group's largest time; channel name / wavelength mismatches between panels sharing
   rendering are reported (`figures.channel_mismatches`, reused by the step-6 check).
6. **Export** (SVG/PDF/TIFF + sidecar JSON + info page) and **consistency check**, then
   archives.
   *Done; archives done in 0.2.0 (`archive.py`, see ARCHITECTURE).* One scene builder (`FigureScene.populate`) for canvas
   and export; the export scene has decorations off and native-resolution rasters. PDF
   rasters are written lossless through `QPainter.LosslessImageRendering` (Qt otherwise
   JPEG-encodes them; the test parses the PDF). Raster exports resample nearest-neighbour by
   default, "smooth" = Qt bilinear. Source data = raw cropped planes in raw orientation,
   native dtype, uncompressed OME-TIFF. Sidecar `<file>.provenance.json` (per format); it
   does not hold the exported files' hashes (they embed its hash); the log entry has both.
   Missing / modified sources refuse the export unless explicitly allowed (logged). The
   consistency check adds three rules beyond §2.9: content past the page edge (outside labels
   estimated from the font size), scale bar longer than its panel, unresolved label tokens.

## 7. Open decisions

1. Free-angle rotation: exclude (v1 proposal) or allow as a flagged resampling step?
2. Panel settings vs image defaults: snapshot at creation (proposal) or live link to the
   library settings until the panel is edited?
3. Lock groups: rely on multi-selection only (OMERO), or also keep persistent "linked"
   rows/columns that propagate edits automatically (handoff §7.2 "lock levels")?
   **Decided: both** — multi-select for editing, `lock_group` for propagation + consistency
   check. **Rule**: a lock shares **black, white, gamma, LUT and invert per channel index**,
   never visibility, T/Z, projection, crop, orientation or labels. An edit of channel *i* on
   a locked panel applies to channel *i* of every panel of its group, values clamped to each
   image's bit depth (the typed value is clamped per image, not copied from another panel's
   clamp); panels lacking channel *i* are skipped. One gesture = one undo step, one log entry
   per panel changed. Locking a selection makes the others take the first panel's locked
   settings, so a group always starts consistent. Replace image does not propagate (its
   level clamp is not an edit). A condition grid with shared rendering is one group; one that
   keeps each image's rendering has one group per row.
4. One figure per tab vs a figure dropdown in a single Figures tab.

## Sources

- OMERO.figure home page: https://openmicroscopy.org/omero/figure
- OMERO guide, *Create figures using OMERO.figure*: https://omero-guides.readthedocs.io/en/latest/figure/docs/omero_figure.html
- OME testing scenarios for OMERO.figure (layout, align, magnification): https://ome-contributing.readthedocs.io/en/latest/testing_scenarios/OMEROfigure.html
- BIOP wiki, OMERO.figure (insets, flip, alignment, scale bar): https://wiki-biop.epfl.ch/en/data-management/omero/omero-figure
- OMERO.figure quick guide (PDF): https://downloads.openmicroscopy.org/help/pdfs/figure.pdf
- OME 2025 workshop slides (dynamic inset, border, HiLo LUT): https://downloads.openmicroscopy.org/presentations/2025/Woods_Hole/Workshop--OME-2025-figure.pptx.pdf
- omero-figure changelog: https://data.safetycli.com/changelogs/omero-figure
