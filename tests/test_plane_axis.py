# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""T vs Z for the single generic plane axis of a TIFF (tifffile "I"):
inference from MetaMorph z-positions, the user override (logged, refused
when the image is in a figure), pinning of the recorded mapping when a file
is reopened, format 5 migration, reports."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from pycrograph.figures import create_figure
from pycrograph.integrity import build_integrity_report
from pycrograph.logchain import verify_log_chain
from pycrograph.migrations import migrate_project_dict
from pycrograph.models import PROJECT_FORMAT_VERSION, ExtractInfo, Panel
from pycrograph.provenance import panel_record
from pycrograph.readers import (
    TiffSource, open_image, open_image_ref, plane_axis_record, recorded_plane_axis,
)
from pycrograph.readers.tiff import MIN_PLANES_FOR_Z, Z_STEP_REL_TOL, regular_z_step
from pycrograph.storage import (
    PlaneAxisError, create_dataset, register_image, set_image_hash, set_plane_axis, sha256_file,
)
from synth import write_metamorph_planes

Z_STACK = [0.5 * i for i in range(7)]                   # 0, 0.5, ..., 3.0 µm
CONSTANT = [1234.5] * 7                                  # time-lapse, fixed focus
AUTOFOCUS = [1234.5, 1234.7, 1234.6, 1234.9, 1234.8, 1235.0, 1234.9]   # drifts both ways


@pytest.fixture
def zstack(data_dir: Path) -> Path:
    return write_metamorph_planes(data_dir / "3_w15 TxRed.TIF", Z_STACK)


@pytest.fixture
def timelapse(data_dir: Path) -> Path:
    return write_metamorph_planes(data_dir / "timelapse.tif", CONSTANT)


def plane_value(src, i: int, axis: str) -> int:
    return int(src.get_plane(t=i if axis == "T" else 0, z=i if axis == "Z" else 0)[0, 0])


# ------------------------------------------------------------------ inference
def test_regular_z_step_rule():
    assert regular_z_step(Z_STACK) == pytest.approx(0.5)
    assert regular_z_step(Z_STACK[::-1]) == pytest.approx(0.5)          # top to bottom
    assert regular_z_step(CONSTANT) is None
    assert regular_z_step(AUTOFOCUS) is None
    assert regular_z_step([0.0, 0.5, None, 1.5]) is None                # a value missing
    assert regular_z_step([0.0, 0.5]) is None and MIN_PLANES_FOR_Z == 3
    assert regular_z_step([0, 1, 2, 3.5]) is None                       # steps 1, 1, 1.5: 20 %
    jitter = [0, 1.0, 2.05, 3.0, 4.02]                                  # piezo jitter: kept
    assert regular_z_step(jitter) == pytest.approx(1.005)
    d = np.abs(np.diff([0, 1, 2, 3.5]))
    assert np.std(d) / np.mean(d) > Z_STEP_REL_TOL
    # Monotonic but irregular (slow thermal drift): not a Z series.
    assert regular_z_step([10.0, 10.1, 10.15, 10.4, 10.45, 10.9]) is None


def test_metamorph_z_stack_is_read_as_z(zstack):
    with open_image(zstack) as src:
        m = src.metadata
        assert src.generic_plane_axis == "I"
        assert (m.shape.T, m.shape.Z, m.shape.C) == (1, 7, 1)
        assert m.calibration.z_step == pytest.approx(0.5)
        assert m.time_offsets_s is None
        assert m.raw["tiff.axis_inference"] == "I→Z (MetaMorph z-position, step 0.5 µm)"
        assert [plane_value(src, i, "Z") for i in range(7)] == list(range(1, 8))
        assert "tiff reader 2" in src.reader_version


@pytest.mark.parametrize("zs", [CONSTANT, AUTOFOCUS], ids=["constant-z", "autofocus-drift"])
def test_time_lapse_stays_t(data_dir, zs):
    path = write_metamorph_planes(data_dir / "tl.tif", zs)
    with open_image(path) as src:
        m = src.metadata
        assert (m.shape.T, m.shape.Z) == (7, 1)
        assert m.calibration.z_step is None
        assert m.time_offsets_s == pytest.approx(list(range(7)))
        assert m.raw["tiff.axis_inference"] == \
            "I→T (MetaMorph z-position not a regular Z series)"
        assert [plane_value(src, i, "T") for i in range(7)] == list(range(1, 8))


def test_two_planes_stay_t(data_dir):
    path = write_metamorph_planes(data_dir / "two.tif", [0.0, 0.5])
    with open_image(path) as src:
        assert (src.shape.T, src.shape.Z) == (2, 1)


def test_non_metamorph_generic_stack_unchanged(data_dir):
    path = data_dir / "plain.tif"
    with tifffile.TiffWriter(path) as tw:
        for i in range(4):
            tw.write(np.full((8, 8), i, np.uint16), metadata=None, contiguous=False)
    with open_image(path) as src:
        assert (src.shape.T, src.shape.Z) == (4, 1)
        assert src.metadata.raw["tiff.axis_inference"] == "I→T (default)"


def test_files_with_their_own_axes_have_no_generic_axis(stk_z, data_dir):
    with open_image(stk_z) as src:
        assert src.generic_plane_axis is None and "tiff.axis_inference" not in src.metadata.raw
    path = data_dir / "hyper.tif"
    tifffile.imwrite(path, np.zeros((3, 2, 8, 8), np.uint16), imagej=True,
                     metadata={"axes": "ZCYX"})
    with open_image(path, plane_axis="T") as src:          # nothing to override
        assert src.generic_plane_axis is None and (src.shape.Z, src.shape.C) == (3, 2)


@pytest.mark.parametrize("path_fixture,forced,shape", [("zstack", "T", (7, 1)),
                                                        ("timelapse", "Z", (1, 7))])
def test_reader_override(request, path_fixture, forced, shape):
    path = request.getfixturevalue(path_fixture)
    with TiffSource(path, plane_axis=forced) as src:
        m = src.metadata
        assert (m.shape.T, m.shape.Z) == shape
        assert m.raw["tiff.axis_inference"] == f"I→{forced} (user override)"
        assert [plane_value(src, i, forced) for i in range(7)] == list(range(1, 8))
        if forced == "Z":
            assert m.time_offsets_s is None and m.calibration.z_step is None   # constant z
        else:
            assert m.calibration.z_step is None
    with pytest.raises(ValueError):
        TiffSource(path, plane_axis="C")


# -------------------------------------------------- reopening never re-infers
def test_import_records_mapping_and_reopen_uses_it(workspace, zstack):
    p = workspace.create_project("P")
    ref = register_image(p, zstack, create_dataset(p, "A").id)
    assert (ref.metadata.shape.T, ref.metadata.shape.Z) == (1, 7)
    assert ref.plane_axis_override is None and recorded_plane_axis(ref) == "Z"
    with open_image_ref(zstack, ref) as src:
        assert src.shape == ref.metadata.shape
        assert src.metadata.raw["tiff.axis_inference"] == "I→Z (as imported)"


def test_image_imported_before_inference_keeps_its_t_shape(workspace, zstack):
    """An ImageRef stored by the previous reader (generic axis -> T, no
    inference string) is never re-interpreted on open / render / Check
    Sources: it stays T=7 until the user overrides it."""
    p = workspace.create_project("Old")
    ref = register_image(p, zstack, create_dataset(p, "A").id)
    with TiffSource(zstack, plane_axis="T") as src:       # what the old reader stored
        ref.metadata = src.metadata
    ref.metadata.raw.pop("tiff.axis_inference")
    assert recorded_plane_axis(ref) == "T"
    assert plane_axis_record(ref)["inference"] == "I→T (imported before plane-axis detection)"
    with open_image_ref(zstack, ref) as src:
        assert (src.shape.T, src.shape.Z) == (7, 1)
        assert int(src.get_plane(t=6)[0, 0]) == 7
    workspace.save_project(p)
    q = workspace.load_project(p.project.id)
    assert (q.images[ref.id].metadata.shape.T, q.images[ref.id].metadata.shape.Z) == (7, 1)


# ------------------------------------------------------------ set_plane_axis
def _project(workspace, path, name="P"):
    p = workspace.create_project(name)
    ref = register_image(p, path, create_dataset(p, "A").id)
    set_image_hash(p, ref.id, sha256_file(path))
    return p, ref


@pytest.mark.parametrize("path_fixture,old,new", [("timelapse", "T", "Z"), ("zstack", "Z", "T")])
def test_override_switches_shape_and_is_logged(request, workspace, path_fixture, old, new):
    path = request.getfixturevalue(path_fixture)
    p, ref = _project(workspace, path)
    before_bytes, sha = path.read_bytes(), ref.sha256
    st = os.stat(path)
    ref.display.t = ref.display.z = 0
    if old == "T":
        ref.display.t = 4
    else:
        ref.display.z = 4
    n = len(p.operation_log)
    assert set_plane_axis(workspace, p, ref, new) is True
    s = ref.metadata.shape
    assert (s.T, s.Z) == ((1, 7) if new == "Z" else (7, 1))
    assert ref.plane_axis_override == new and recorded_plane_axis(ref) == new
    assert ref.metadata.raw["tiff.axis_inference"] == f"I→{new} (user override)"
    assert (ref.display.t, ref.display.z) == (0, 0)
    with open_image_ref(path, ref) as src:
        assert src.shape == s
        assert [plane_value(src, i, new) for i in range(7)] == list(range(1, 8))
    [e] = p.operation_log[n:]
    assert (e.operation, e.target_type, e.target_id, e.field, e.old_value, e.new_value) == (
        "image.set", "image", ref.id, "plane_axis", old, new)
    assert e.asset_sha256 == sha and "user override" in e.note
    assert verify_log_chain(p).status == "ok"
    # Raw file and hash untouched.
    assert path.read_bytes() == before_bytes and ref.sha256 == sha == sha256_file(path)
    assert os.stat(path).st_mtime_ns == st.st_mtime_ns
    # Same choice again: nothing to do, nothing logged.
    assert set_plane_axis(workspace, p, ref, new) is False and len(p.operation_log) == n + 1
    # Round trip.
    workspace.save_project(p)
    q = workspace.load_project(p.project.id)
    r = q.images[ref.id]
    assert r.plane_axis_override == new and r.metadata.shape == s


def test_override_refused_when_image_is_in_a_figure(workspace, timelapse):
    p, ref = _project(workspace, timelapse)
    for name in ("Figure 1", "Figure 2"):
        create_figure(p, name).panels.append(Panel(id=name, image_id=ref.id, width_mm=20,
                                                   height_mm=20))
    n = len(p.operation_log)
    with pytest.raises(PlaneAxisError) as exc:
        set_plane_axis(workspace, p, ref, "Z")
    msg = str(exc.value)
    assert "“Figure 1”, “Figure 2”" in msg and "not supported" in msg
    assert ref.metadata.shape.T == 7 and ref.plane_axis_override is None
    assert len(p.operation_log) == n


def test_override_refused_without_generic_axis(workspace, mm_tiff, stk_tc, data_dir):
    p, single = _project(workspace, mm_tiff)
    with pytest.raises(PlaneAxisError, match="single plane"):
        set_plane_axis(workspace, p, single, "Z")
    stk = register_image(p, stk_tc)
    with pytest.raises(PlaneAxisError, match="only a generic plane axis can be switched"):
        set_plane_axis(workspace, p, stk, "Z")
    with pytest.raises(ValueError):
        set_plane_axis(workspace, p, stk, "C")


def test_override_refused_for_extract_and_changed_file(workspace, timelapse):
    p, ref = _project(workspace, timelapse)
    ref.extract = ExtractInfo(sha256="0" * 64, origin_x=0, origin_y=0, width=4, height=4,
                              t=[0], z=[0], c=[0])
    with pytest.raises(PlaneAxisError, match="light archive"):
        set_plane_axis(workspace, p, ref, "Z")
    ref.extract = None
    os.utime(timelapse, ns=(ref.mtime_ns + 10**9, ref.mtime_ns + 10**9))
    with pytest.raises(PlaneAxisError, match="changed since import"):
        set_plane_axis(workspace, p, ref, "Z")


# -------------------------------------------------------- migration, reports
def test_v4_to_v5_migration(workspace, zstack):
    p, ref = _project(workspace, zstack)
    workspace.save_project(p)
    path = workspace.project_json(p.project.id)
    d = json.loads(path.read_text())
    d["project"]["format_version"] = 4
    d["images"][ref.id].pop("plane_axis_override")
    path.write_text(json.dumps(d))
    out, old = migrate_project_dict(json.loads(path.read_text()))
    assert old == 4 and out["images"][ref.id]["plane_axis_override"] is None
    q = workspace.load_project(p.project.id)
    assert q.project.format_version == PROJECT_FORMAT_VERSION == 5
    assert q.images[ref.id].plane_axis_override is None
    assert q.images[ref.id].metadata.shape.Z == 7                       # as imported
    last = q.operation_log[-1]
    assert last.operation == "project.migrate" and (last.old_value, last.new_value) == (4, 5)
    assert verify_log_chain(q).status == "ok"


def test_integrity_report_and_provenance_record_the_mapping(workspace, timelapse, zstack):
    p, ref = _project(workspace, timelapse)
    zref = register_image(p, zstack)
    set_plane_axis(workspace, p, ref, "Z")
    report = build_integrity_report(workspace, p)
    rec = {im["filename"]: im["plane_axis"] for im in report["images"]}
    assert rec["timelapse.tif"] == {"axis": "Z", "override": "Z",
                                    "inference": "I→Z (user override)"}
    assert rec["3_w15 TxRed.TIF"] == {"axis": "Z", "override": None,
                                      "inference": "I→Z (MetaMorph z-position, step 0.5 µm)"}
    fig = create_figure(p, "F")
    panel = Panel(id="pz", image_id=zref.id, width_mm=20, height_mm=20)
    fig.panels.append(panel)
    assert panel_record(p, fig, panel, 1)["image"]["plane_axis"]["axis"] == "Z"
