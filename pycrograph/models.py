# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Pydantic data model for a Pycrograph project file (project.json).

Hierarchy (logical, OMERO-style — not a mirror of disk folders):

    Project
      ├── datasets: [Dataset]            (each lists image ids; an image may
      │                                    sit in several datasets)
      ├── images: {id: ImageRef}         (raw files registered BY REFERENCE)
      ├── figures: [Figure]              (canvas pages made of Panels)
      └── operation_log: [OperationLogEntry]  (hash-chained, see logchain.py)

Raw pixels are never modified. Everything shown or exported is re-rendered
from the raw file + the display settings stored here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Bump when the on-disk schema changes incompatibly; storage.load_project
# refuses files with a higher version than it knows.
PROJECT_FORMAT_VERSION = 2   # 2: Figure.page is a PageSetup (see migrations.py)

Rotation = Literal[0, 90, 180, 270]

# --------------------------------------------------------------------------
# Image metadata (parsed by a reader at import time, stored for display)
# --------------------------------------------------------------------------


class Shape5D(BaseModel):
    """Canonical dimension sizes. Every reader maps its file onto T, Z, C, Y, X."""
    T: int = Field(1, ge=1)
    Z: int = Field(1, ge=1)
    C: int = Field(1, ge=1)
    Y: int = Field(ge=1)
    X: int = Field(ge=1)

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (self.T, self.Z, self.C, self.Y, self.X)


class Calibration(BaseModel):
    pixel_size_x: Optional[float] = None   # in `unit` per pixel
    pixel_size_y: Optional[float] = None
    z_step: Optional[float] = None
    unit: str = "um"
    # Where the numbers came from: "metamorph-xml", "stk-uic", "tiff-resolution",
    # "user" or "none". Recorded so provenance can say how a scale bar was derived.
    source: str = "none"

    @property
    def is_calibrated(self) -> bool:
        return bool(self.pixel_size_x) and bool(self.pixel_size_y)


class ChannelInfo(BaseModel):
    """What the file says about one channel (never edited by the user)."""
    index: int = Field(ge=0)
    name: str = ""                          # e.g. MetaMorph _IllumSetting_ "5 TxRed"
    wavelength_nm: Optional[float] = None
    exposure_ms: Optional[float] = None


class ImageMetadata(BaseModel):
    shape: Shape5D
    dtype: Literal["uint8", "uint16"]
    # Significant bits (MetaMorph bits-per-pixel, else dtype width). Sets the
    # saturation ceiling (2**bit_depth - 1) and the levels range.
    bit_depth: int = Field(ge=1, le=16)
    calibration: Calibration = Field(default_factory=Calibration)
    channels: List[ChannelInfo] = Field(default_factory=list)
    objective: Optional[str] = None
    numerical_aperture: Optional[float] = None
    acquisition_time: Optional[str] = None          # ISO 8601, local time as written by the instrument
    # Seconds from the first time point, one per T (None when unknown).
    time_offsets_s: Optional[List[float]] = None
    # Selected raw key/values kept verbatim for provenance and the info panel.
    raw: Dict[str, Any] = Field(default_factory=dict)

    @property
    def full_scale(self) -> int:
        return (1 << self.bit_depth) - 1


# --------------------------------------------------------------------------
# Display settings (display-only, re-applied to raw pixels at every render)
# --------------------------------------------------------------------------


class ChannelSettings(BaseModel):
    index: int = Field(ge=0)
    visible: bool = True
    # Named LUT ("grey", "red", "green", "blue", "cyan", "magenta", "yellow")
    # or a "#RRGGBB" colour. See display.lut_rgb.
    lut: str = "grey"
    black: int = Field(0, ge=0, le=65535)
    white: int = Field(65535, ge=1, le=65535)
    gamma: float = Field(1.0, gt=0)     # same convention as PysternBlot: out = x ** (1/gamma)
    invert: bool = False


class ImageDisplay(BaseModel):
    """Per-image viewing state in the library viewer. Panels copy it."""
    channels: List[ChannelSettings] = Field(default_factory=list)
    t: int = Field(0, ge=0)
    z: int = Field(0, ge=0)
    rotation_deg: Rotation = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False


class SaturationStats(BaseModel):
    """Same fields as PysternBlot's SaturationStats (uint sources only)."""
    max_value: int
    full_scale: int
    saturated_count: int
    total_pixels: int
    saturated_fraction: float
    solid_saturated_count: int   # pixels surviving 3x3 erosion of the mask


# --------------------------------------------------------------------------
# Library: images registered by reference, datasets
# --------------------------------------------------------------------------


class ImageRef(BaseModel):
    id: str
    filename: str
    # Absolute path the image is read from. For an image copied into the
    # project, `stored_relpath` wins and `path` keeps the original location.
    path: str
    original_path: str
    stored_relpath: Optional[str] = None
    # None while the background hash is still running.
    sha256: Optional[str] = None
    size_bytes: int = Field(ge=0)
    mtime_ns: int
    reader: str
    reader_version: str
    imported_utc: str
    metadata: ImageMetadata
    display: ImageDisplay = Field(default_factory=ImageDisplay)

    # User annotations (searchable/filterable in the library)
    tags: List[str] = Field(default_factory=list)
    key_values: Dict[str, str] = Field(default_factory=dict)
    rating: int = Field(0, ge=0, le=5)
    # Free text per channel index, e.g. {0: "anti-Lamin A/C (Alexa 594)"}.
    # The PysternBlot "antibody name per blot" equivalent.
    channel_stains: Dict[int, str] = Field(default_factory=dict)

    # Import-time warnings recorded for the integrity report (8-bit, ...).
    warnings: List[str] = Field(default_factory=list)


class Dataset(BaseModel):
    id: str
    name: str
    description: str = ""
    image_ids: List[str] = Field(default_factory=list)
    hidden: bool = False


# --------------------------------------------------------------------------
# Figures (canvas). See docs/design/figure-editor.md §3.
# Kept independent of microscopy specifics where possible so the layout code
# can become shared-core with PysternBlot's planned "structured figure
# composition".
# --------------------------------------------------------------------------


class Crop(BaseModel):
    """Rectangle in raw-image pixel coordinates (before rotation/flip)."""
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class PanelLabel(BaseModel):
    text: str = ""
    # "channel:<i>", "time", "z", "tag:<name>", "kv:<key>" or "" (free text)
    source: str = ""
    position: Literal[
        "top", "bottom", "left", "right", "left-vertical", "right-vertical",
        "top-left", "top-right", "bottom-left", "bottom-right",
    ] = "top"
    font_size_pt: Optional[float] = None
    color: Optional[str] = None          # None = channel colour / black


class ScaleBar(BaseModel):
    visible: bool = False
    length: float = 10.0                 # in calibration units
    unit: str = "um"
    position: Literal["bottom-left", "bottom-right", "top-left", "top-right"] = "bottom-right"
    thickness_pt: float = 2.0
    show_label: bool = True
    color: str = "#FFFFFF"
    font_size_pt: Optional[float] = None         # None = page preset font size
    label_position: Literal["below", "above"] = "below"


class ZProjection(BaseModel):
    method: Literal["max"] = "max"
    z_start: int = Field(ge=0)
    z_end: int = Field(ge=0)             # inclusive


class PanelBorder(BaseModel):
    visible: bool = False
    color: str = "#FFFFFF"
    width_pt: float = Field(0.5, gt=0)


class InsetStyle(BaseModel):
    """Style and links of an inset panel (Panel.inset_of is set). The
    rectangle on the parent is derived from the inset's crop at render time."""
    color: str = "#FFFFFF"
    width_pt: float = Field(0.75, gt=0)
    show_on_parent: bool = True
    follow_parent_tz: bool = True
    follow_parent_rendering: bool = True


class Shape(BaseModel):
    """Vector shape drawn on a panel, in raw-image pixel coordinates, so it
    follows the crop and zoom. Annotation only — never touches pixels."""
    id: str
    kind: Literal["arrow", "line", "rect", "ellipse"]
    points: List[tuple[float, float]] = Field(default_factory=list)
    color: str = "#FFFFFF"
    width_pt: float = Field(1.0, gt=0)            # page points: equal on every panel


class Panel(BaseModel):
    id: str
    image_id: str
    # Page index (0-based) and position/size on that page, in millimetres.
    page: int = Field(0, ge=0)
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = Field(40.0, gt=0)
    height_mm: float = Field(40.0, gt=0)
    t: int = Field(0, ge=0)
    z: int = Field(0, ge=0)
    z_projection: Optional[ZProjection] = None
    channels: List[ChannelSettings] = Field(default_factory=list)
    # Viewport = crop, in raw pixels (None = the whole image).
    crop: Optional[Crop] = None
    rotation_deg: Rotation = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    labels: List[PanelLabel] = Field(default_factory=list)
    scale_bar: ScaleBar = Field(default_factory=ScaleBar)
    border: PanelBorder = Field(default_factory=PanelBorder)
    shapes: List[Shape] = Field(default_factory=list)
    # Offsets for [time] / [z] label tokens (t = 0 at drug addition, ...).
    time_offset_s: float = 0.0
    z_offset: float = 0.0
    # Panels sharing a non-empty lock group share levels/LUT (row/column lock).
    lock_group: Optional[str] = None
    # Inset support: parent panel id; the region shown is this panel's crop.
    inset_of: Optional[str] = None
    inset_style: Optional[InsetStyle] = None


class PagePreset(BaseModel):
    name: str = "Nature double column"
    width_mm: float = Field(183.0, gt=0)
    max_height_mm: float = Field(170.0, gt=0)
    dpi: int = Field(300, ge=72)
    font_family: str = "Arial"
    font_size_pt: float = 8.0
    min_line_pt: float = 0.5


class PageSetup(BaseModel):
    """Paper of a figure: a journal preset plus the actual page size.
    width_mm / height_mm are the portrait size; landscape swaps them."""
    preset: PagePreset = Field(default_factory=PagePreset)
    width_mm: float = Field(183.0, gt=0)
    height_mm: float = Field(170.0, gt=0)
    orientation: Literal["portrait", "landscape"] = "portrait"
    n_pages: int = Field(1, ge=1)
    background: str = "#FFFFFF"
    grid_mm: float = Field(1.0, gt=0)
    snap: bool = True

    @classmethod
    def from_preset(cls, preset: PagePreset, **kw: Any) -> "PageSetup":
        return cls(preset=preset, width_mm=preset.width_mm, height_mm=preset.max_height_mm, **kw)

    @property
    def size_mm(self) -> tuple[float, float]:
        """(width, height) as laid out on screen and paper."""
        if self.orientation == "landscape":
            return self.height_mm, self.width_mm
        return self.width_mm, self.height_mm


class Overlay(BaseModel):
    """Vector annotation (annotation only — never touches pixels)."""
    id: str
    kind: Literal["arrow", "line", "rect", "ellipse"]
    panel_id: Optional[str] = None       # None = page coordinates
    points: List[tuple[float, float]] = Field(default_factory=list)
    color: str = "#FFFFFF"
    width_pt: float = 1.0


class Figure(BaseModel):
    id: str
    name: str
    page: PageSetup = Field(default_factory=PageSetup)
    panels: List[Panel] = Field(default_factory=list)
    overlays: List[Overlay] = Field(default_factory=list)
    legend: str = ""                     # goes to the export info page
    hidden: bool = False

    def panel(self, panel_id: str) -> Optional[Panel]:
        return next((p for p in self.panels if p.id == panel_id), None)


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------


class ProjectMeta(BaseModel):
    id: str
    name: str
    created_utc: str
    modified_utc: Optional[str] = None
    app_version: str
    format_version: int = PROJECT_FORMAT_VERSION
    license: Literal["GPL-3.0-only", "GPL-3.0-or-later"] = "GPL-3.0-only"
    # Soft-hide (restorable) instead of deleting, as in PysternBlot.
    is_archived: bool = False


class OperationLogEntry(BaseModel):
    """Identical shape to PysternBlot's OperationLogEntry so logchain.py is
    shared verbatim."""
    timestamp_utc: str
    operation: str

    target_type: Optional[str] = None   # "project", "dataset", "image", "panel", "figure", "export"
    target_id: Optional[str] = None
    asset_sha256: Optional[str] = None

    field: Optional[str] = None         # e.g. "display.channels[0].white"
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None

    note: Optional[str] = None

    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None


class Project(BaseModel):
    project: ProjectMeta
    datasets: List[Dataset] = Field(default_factory=list)
    images: Dict[str, ImageRef] = Field(default_factory=dict)
    figures: List[Figure] = Field(default_factory=list)
    operation_log: List[OperationLogEntry] = Field(default_factory=list)

    # ---- convenience lookups -------------------------------------------
    def dataset(self, dataset_id: str) -> Optional[Dataset]:
        return next((d for d in self.datasets if d.id == dataset_id), None)

    def datasets_containing(self, image_id: str) -> List[Dataset]:
        return [d for d in self.datasets if image_id in d.image_ids]

    def figure(self, figure_id: str) -> Optional[Figure]:
        return next((f for f in self.figures if f.id == figure_id), None)

