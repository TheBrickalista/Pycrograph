# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Reader layer: MetaMorph TIFF / STK / plain TIFF -> (T, Z, C, Y, X), lazily."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from pycrograph.readers import UnsupportedImageError, open_image, sniff_format
from pycrograph.readers.metamorph import (
    parse_description, parse_metamorph_time, parse_metamorph_xml, plane_summary,
)
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff


# ------------------------------------------------------------ XML parsing
def test_metamorph_xml_reference_fields():
    s = plane_summary(parse_metamorph_xml(metamorph_xml()))
    assert s.calibrated and s.pixel_size_x == pytest.approx(0.325) and s.unit == "um"
    assert s.illumination == "5 TxRed"
    assert s.wavelength_nm == 630
    assert s.objective == "40xOIL" and s.numerical_aperture == pytest.approx(1.3)
    assert s.exposure_ms == pytest.approx(500)
    assert s.acquisition_time == "2025-10-29T15:42:11.513"
    assert s.bits_per_pixel == 16
    assert s.application == "MetaMorph 7.8.5.0"


def test_calibration_off_is_not_used():
    xml = metamorph_xml().replace('id="spatial-calibration-state" type="bool" value="on"',
                                  'id="spatial-calibration-state" type="bool" value="off"')
    assert not plane_summary(parse_metamorph_xml(xml)).calibrated


def test_xml_with_doctype_is_refused():
    with pytest.raises(ValueError):
        parse_metamorph_xml('<!DOCTYPE x [<!ENTITY a "b">]><MetaData/>')


def test_key_value_description_and_exposure_units():
    props = parse_description("Exposure: 1.5 s\r\nIllumination: DAPI\r\nWavelength: 405 nm")
    s = plane_summary(props)
    assert s.exposure_ms == pytest.approx(1500) and s.illumination == "DAPI"
    assert s.wavelength_nm == 405


def test_time_parse():
    assert parse_metamorph_time("20240102 03:04:05") == "2024-01-02T03:04:05"
    assert parse_metamorph_time("garbage") is None


# ---------------------------------------------------------- MetaMorph TIFF
def test_metamorph_tiff_metadata_and_lazy_plane(mm_tiff: Path):
    with open_image(mm_tiff) as src:
        m = src.metadata
        assert src.reader_name == "metamorph-tiff"
        assert src.access_mode == "memmap"
        assert m.shape.as_tuple() == (1, 1, 1, 64, 64)
        assert m.dtype == "uint16" and m.bit_depth == 16 and m.full_scale == 65535
        assert m.calibration.pixel_size_x == pytest.approx(0.325)
        assert m.calibration.source == "metamorph-xml"
        assert m.channels[0].name == "5 TxRed" and m.channels[0].wavelength_nm == 630
        assert m.channels[0].exposure_ms == pytest.approx(500)
        assert m.objective == "40xOIL" and m.numerical_aperture == pytest.approx(1.3)
        assert m.acquisition_time.startswith("2025-10-29T15:42")
        plane = src.get_plane(0, 0, 0)
        assert plane.dtype == np.uint16 and plane.shape == (64, 64)
        assert not plane.flags.writeable                  # raw pixels are never modified
        assert 2412 <= int(plane.min()) and int(plane.max()) <= 13989
        np.testing.assert_array_equal(plane, tifffile.imread(mm_tiff))


def test_out_of_range_index(mm_tiff: Path):
    with open_image(mm_tiff) as src, pytest.raises(IndexError):
        src.get_plane(1, 0, 0)


def test_multipage_metamorph_tiff_time_offsets(data_dir: Path):
    planes = [reference_like_plane(32, 32, seed=i) for i in range(3)]
    xmls = [metamorph_xml(width=32, height=32, acq=f"20251029 15:42:{10 + 5 * i:02d}.000")
            for i in range(3)]
    path = write_metamorph_tiff(data_dir / "multi page.tif", planes, xmls)
    with open_image(path) as src:
        assert src.shape.T == 3 and src.shape.C == 1
        assert src.metadata.time_offsets_s == pytest.approx([0, 5, 10])
        for i in range(3):
            np.testing.assert_array_equal(src.get_plane(i), planes[i])


# -------------------------------------------------------------------- STK
def test_stk_time_lapse_two_channels(stk_tc: Path):
    with open_image(stk_tc) as src:
        m = src.metadata
        assert src.reader_name == "metamorph-stk" and src.access_mode == "memmap"
        assert m.shape.as_tuple() == (3, 1, 2, 24, 32)
        assert [c.wavelength_nm for c in m.channels] == [488, 561]
        assert [c.name for c in m.channels] == ["GFP", "TxRed"]
        assert m.calibration.pixel_size_x == pytest.approx(0.325)
        assert m.calibration.source == "stk-uic"
        assert m.time_offsets_s == pytest.approx([0, 30, 60])
        for t in range(3):
            for c in range(2):
                assert int(src.get_plane(t, 0, c)[0, 0]) == t * 2 + c   # interleaved order


def test_stk_z_stack(stk_z: Path):
    with open_image(stk_z) as src:
        assert src.shape.as_tuple() == (1, 5, 1, 16, 16)
        assert src.metadata.calibration.z_step == pytest.approx(0.5)
        assert int(src.get_plane(0, 3, 0)[0, 0]) == 503


def test_stk_reads_lazily(stk_tc: Path, monkeypatch):
    """get_plane must not decode the whole series."""
    with open_image(stk_tc) as src:
        def boom(*a, **k):
            raise AssertionError("whole-series read")
        monkeypatch.setattr(tifffile.TiffPageSeries, "asarray", boom)
        monkeypatch.setattr(tifffile.TiffFile, "asarray", boom)
        src.get_plane(2, 0, 1)


# ------------------------------------------------------------ plain TIFF
def test_imagej_hyperstack_axes_and_calibration(data_dir: Path):
    arr = np.arange(2 * 3 * 8 * 10, dtype=np.uint16).reshape(2, 3, 8, 10)
    path = data_dir / "hyper.tif"
    tifffile.imwrite(path, arr, imagej=True, resolution=(1 / 0.2, 1 / 0.2),
                     metadata={"axes": "TCYX", "unit": "um"})
    with open_image(path) as src:
        assert src.reader_name == "tiff"
        assert src.shape.as_tuple() == (2, 1, 3, 8, 10)
        assert src.metadata.calibration.pixel_size_x == pytest.approx(0.2, rel=1e-4)
        np.testing.assert_array_equal(src.get_plane(1, 0, 2), arr[1, 2])


def test_compressed_multipage_uses_page_reads(data_dir: Path):
    path = data_dir / "zlib.tif"
    planes = [np.full((8, 8), i, np.uint16) for i in range(4)]
    with tifffile.TiffWriter(path) as tw:
        for p in planes:
            tw.write(p, compression="zlib", metadata=None, contiguous=False)
    with open_image(path) as src:
        assert src.access_mode == "pages"
        assert src.shape.T == 4
        assert int(src.get_plane(3)[0, 0]) == 3


def test_8bit_accepted(data_dir: Path):
    path = data_dir / "eight.tif"
    tifffile.imwrite(path, np.arange(64, dtype=np.uint8).reshape(8, 8))
    with open_image(path) as src:
        assert src.metadata.dtype == "uint8" and src.metadata.full_scale == 255


def test_float_rejected(data_dir: Path):
    path = data_dir / "float.tif"
    tifffile.imwrite(path, np.zeros((4, 4), np.float32))
    with pytest.raises(UnsupportedImageError):
        open_image(path)


def test_jpeg_rejected(data_dir: Path):
    path = data_dir / "photo.tif"                        # extension lies; magic bytes don't
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    assert sniff_format(path) == "jpeg"
    with pytest.raises(UnsupportedImageError, match="JPEG"):
        open_image(path)


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        open_image(tmp_path / "nope.tif")


# --------------------------------------------------------- real sample file
SAMPLE = os.environ.get("PYCROGRAPH_SAMPLE_TIFF")


@pytest.mark.sample
@pytest.mark.skipif(not SAMPLE, reason="set PYCROGRAPH_SAMPLE_TIFF=/path/to/Nuc_ER_ER.tif")
def test_reference_sample_file():
    with open_image(SAMPLE) as src:
        m = src.metadata
        assert src.reader_name == "metamorph-tiff"
        assert (m.shape.Y, m.shape.X) == (1024, 1024) and m.dtype == "uint16"
        assert m.calibration.pixel_size_x == pytest.approx(0.325)
        assert m.channels[0].wavelength_nm == 630
        assert "TxRed" in m.channels[0].name
        assert m.objective == "40xOIL"
        plane = src.get_plane()
        assert 2000 < int(plane.min()) < int(plane.max()) < 65535
