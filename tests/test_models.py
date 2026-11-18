# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Pydantic models: JSON round-trip and validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pycrograph.jsonsafe import dumps_strict
from pycrograph.models import (
    PROJECT_FORMAT_VERSION, ChannelInfo, ChannelSettings, Crop, Dataset, Figure, ImageDisplay,
    ImageMetadata, ImageRef, Overlay, PagePreset, Panel, PanelLabel, Project, ProjectMeta,
    ScaleBar, Shape5D, ZProjection,
)
from pycrograph.storage import ProjectFormatError, load_project_file


def full_project() -> Project:
    meta = ImageMetadata(shape=Shape5D(T=3, Z=1, C=2, Y=512, X=512), dtype="uint16", bit_depth=16,
                         channels=[ChannelInfo(index=0, name="GFP", wavelength_nm=488),
                                   ChannelInfo(index=1, name="5 TxRed", wavelength_nm=630,
                                               exposure_ms=500)],
                         time_offsets_s=[0.0, 30.0, 60.0], raw={"_MagSetting_": "40xOIL"})
    chans = [ChannelSettings(index=0, lut="green", black=2400, white=9000),
             ChannelSettings(index=1, lut="#FF00AA", black=2500, white=14000, gamma=1.2, invert=True)]
    ref = ImageRef(id="img1", filename="a b.stk", path="/Volumes/LaCie/a b.stk",
                   original_path="/Volumes/LaCie/a b.stk", sha256="0" * 64, size_bytes=10,
                   mtime_ns=1_700_000_000_123_456_789, reader="metamorph-stk",
                   reader_version="x", imported_utc="2026-11-18T00:00:00+00:00", metadata=meta,
                   display=ImageDisplay(channels=chans, t=2, rotation_deg=90, flip_vertical=True),
                   tags=["ctrl", "rep1"], key_values={"cell line": "MCF10A"}, rating=4,
                   channel_stains={1: "anti-Lamin A/C"})
    panel = Panel(id="p1", image_id="img1", channels=chans, crop=Crop(x=10, y=20, w=100, h=80),
                  z_projection=ZProjection(z_start=0, z_end=0),
                  labels=[PanelLabel(text="GFP", source="channel:0", position="top-left")],
                  scale_bar=ScaleBar(visible=True, length=10), lock_group="row1")
    fig = Figure(id="f1", name="Fig 1", page=PagePreset(), panels=[panel],
                 overlays=[Overlay(id="o1", kind="arrow", panel_id="p1", points=[(1, 2), (3, 4)])])
    return Project(project=ProjectMeta(id="proj1", name="P", created_utc="2026-11-18T00:00:00+00:00",
                                       app_version="0.1.0"),
                   datasets=[Dataset(id="d1", name="Ctrl", image_ids=["img1"])],
                   images={"img1": ref}, figures=[fig])


def test_round_trip_identical(tmp_path):
    p = full_project()
    path = tmp_path / "project.json"
    path.write_text(dumps_strict(p.model_dump(mode="json"), indent=2))
    q = load_project_file(path)
    assert q == p
    assert q.images["img1"].channel_stains == {1: "anti-Lamin A/C"}     # int keys restored
    assert q.images["img1"].mtime_ns == 1_700_000_000_123_456_789        # no float precision loss
    assert q.figures[0].overlays[0].points == [(1, 2), (3, 4)]


def test_validation():
    with pytest.raises(ValidationError):
        ChannelSettings(index=0, white=70000)
    with pytest.raises(ValidationError):
        ChannelSettings(index=0, gamma=0)
    with pytest.raises(ValidationError):
        ImageDisplay(rotation_deg=45)
    p = full_project()
    data = p.images["img1"].model_dump()
    data["rating"] = 6
    with pytest.raises(ValidationError):
        ImageRef.model_validate(data)


def test_newer_format_refused(tmp_path):
    p = full_project()
    d = p.model_dump(mode="json")
    d["project"]["format_version"] = PROJECT_FORMAT_VERSION + 1
    path = tmp_path / "project.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectFormatError):
        load_project_file(path)


def test_full_scale():
    m = ImageMetadata(shape=Shape5D(Y=1, X=1), dtype="uint16", bit_depth=12)
    assert m.full_scale == 4095
