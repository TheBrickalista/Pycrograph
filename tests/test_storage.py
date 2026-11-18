# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Import by reference, hashing, relink, datasets, annotations, save/load."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import tifffile

from pycrograph.integrity import build_integrity_report, write_integrity_json
from pycrograph.logchain import verify_log_chain
from pycrograph.storage import (
    RefStatus, RelinkError, add_image_to_dataset, check_image, common_prefix_change,
    copy_into_project, create_dataset, register_image, relink_by_prefix, relink_image,
    remove_image_from_dataset, search_images, set_image_annotation, set_image_hash, sha256_file,
    write_thumbnail,
)
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff


def _hashed(project, ref):
    set_image_hash(project, ref.id, sha256_file(ref.path))
    return ref


def test_register_by_reference_no_copy(workspace, mm_tiff):
    p = workspace.create_project("P")
    ds = create_dataset(p, "Ctrl")
    ref = register_image(p, mm_tiff, ds.id)
    assert ref.path == str(mm_tiff.resolve()) and ref.sha256 is None
    assert ref.size_bytes == mm_tiff.stat().st_size and ref.mtime_ns == mm_tiff.stat().st_mtime_ns
    assert ref.reader == "metamorph-tiff" and "tifffile" in ref.reader_version
    assert ds.image_ids == [ref.id]
    # Nothing copied into the workspace.
    assert not (workspace.project_dir(p.project.id) / "images").exists()
    # Default display: grey for one channel, window inside the data range.
    cs = ref.display.channels[0]
    assert cs.lut == "grey" and 2412 <= cs.black < cs.white <= 13989
    assert ref.warnings == []
    assert check_image(workspace, p, ref) == RefStatus.PENDING_HASH


def test_same_file_in_two_datasets_no_duplicate(workspace, mm_tiff):
    p = workspace.create_project("P")
    a, b = create_dataset(p, "A"), create_dataset(p, "B")
    r1 = register_image(p, mm_tiff, a.id)
    r2 = register_image(p, mm_tiff, b.id)
    assert r1 is r2 and len(p.images) == 1
    assert [d.name for d in p.datasets_containing(r1.id)] == ["A", "B"]
    remove_image_from_dataset(p, r1.id, a.id)
    assert r1.id in p.images and a.image_ids == []
    add_image_to_dataset(p, r1.id, a.id)
    assert a.image_ids == [r1.id]


def test_8bit_warning(workspace, data_dir):
    path = data_dir / "eight.tif"
    tifffile.imwrite(path, np.arange(64, dtype=np.uint8).reshape(8, 8))
    ref = register_image(workspace.create_project("P"), path)
    assert any("8-bit" in w for w in ref.warnings)


def test_hash_and_status(workspace, mm_tiff):
    p = workspace.create_project("P")
    ref = _hashed(p, register_image(p, mm_tiff))
    assert check_image(workspace, p, ref) == RefStatus.OK
    assert check_image(workspace, p, ref, verify_hash=True) == RefStatus.OK

    # Touch the date only: cheap check says CHANGED, hashing says OK.
    st = mm_tiff.stat()
    os.utime(mm_tiff, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert check_image(workspace, p, ref) == RefStatus.CHANGED
    assert check_image(workspace, p, ref, verify_hash=True) == RefStatus.OK

    # Change content: MODIFIED.
    data = bytearray(mm_tiff.read_bytes())
    data[-1] ^= 0xFF
    mm_tiff.write_bytes(bytes(data))
    assert check_image(workspace, p, ref, verify_hash=True) == RefStatus.MODIFIED

    mm_tiff.unlink()
    assert check_image(workspace, p, ref) == RefStatus.MISSING


def test_set_hash_refuses_overwrite(workspace, mm_tiff):
    p = workspace.create_project("P")
    ref = _hashed(p, register_image(p, mm_tiff))
    with pytest.raises(ValueError):
        set_image_hash(p, ref.id, "f" * 64)


def test_relink_verified_by_hash(workspace, mm_tiff, tmp_path):
    p = workspace.create_project("P")
    ref = _hashed(p, register_image(p, mm_tiff))
    moved = tmp_path / "elsewhere" / "Nuc_ER_ER.tif"
    moved.parent.mkdir()
    shutil.move(mm_tiff, moved)
    assert check_image(workspace, p, ref) == RefStatus.MISSING
    relink_image(p, ref.id, moved)
    assert ref.path == str(moved.resolve())
    assert check_image(workspace, p, ref) == RefStatus.OK
    assert p.operation_log[-1].operation == "image.relink"


def test_relink_refuses_other_content(workspace, mm_tiff, data_dir):
    p = workspace.create_project("P")
    ref = _hashed(p, register_image(p, mm_tiff))
    other = write_metamorph_tiff(data_dir / "other.tif", [reference_like_plane(64, 64, seed=9)],
                                 [metamorph_xml(width=64, height=64)])
    assert other.stat().st_size == mm_tiff.stat().st_size     # same size, different pixels
    with pytest.raises(RelinkError, match="SHA-256"):
        relink_image(p, ref.id, other)
    assert ref.path == str(mm_tiff.resolve())


def test_relink_without_hash_needs_explicit_unverified(workspace, mm_tiff):
    p = workspace.create_project("P")
    ref = register_image(p, mm_tiff)
    with pytest.raises(RelinkError):
        relink_image(p, ref.id, mm_tiff)
    relink_image(p, ref.id, mm_tiff, allow_unverified=True)
    assert ref.sha256 == sha256_file(mm_tiff)
    assert "UNVERIFIED" in p.operation_log[-1].note


def test_relink_by_prefix_drive_renamed(workspace, tmp_path):
    """/…/LaCie -> /…/LaCie 1 (macOS remount), folder names with spaces."""
    p = workspace.create_project("P")
    root = tmp_path / "Volumes" / "LaCie"
    files = []
    for i, sub in enumerate(["IF 2025/exp 1", "IF 2025/exp 2", "live/cell a"]):
        d = root / sub
        d.mkdir(parents=True)
        files.append(write_metamorph_tiff(d / f"img {i}.tif", [reference_like_plane(16, 16, seed=i)],
                                          [metamorph_xml(width=16, height=16)]))
    refs = [_hashed(p, register_image(p, f)) for f in files]
    new_root = tmp_path / "Volumes" / "LaCie 1"
    root.rename(new_root)
    assert all(check_image(workspace, p, r) == RefStatus.MISSING for r in refs)

    first_new = new_root / "IF 2025" / "exp 1" / "img 0.tif"
    relink_image(p, refs[0].id, first_new)
    old_prefix, new_prefix = common_prefix_change(str(files[0].resolve()), refs[0].path)
    assert old_prefix.endswith("LaCie") and new_prefix.endswith("LaCie 1")
    done = relink_by_prefix(workspace, p, old_prefix, new_prefix)
    assert set(done) == {refs[1].id, refs[2].id}
    assert all(check_image(workspace, p, r) == RefStatus.OK for r in refs)
    assert verify_log_chain(p).status == "ok"


def test_copy_into_project(workspace, mm_tiff):
    p = workspace.create_project("P")
    ref = register_image(p, mm_tiff)
    copy_into_project(workspace, p, ref.id)
    stored = workspace.resolve_image_path(p, ref)
    assert stored.is_file() and stored != Path(ref.path)
    assert sha256_file(stored) == ref.sha256 == sha256_file(mm_tiff)
    mm_tiff.unlink()                                   # original gone: project still self-contained
    assert check_image(workspace, p, ref) == RefStatus.OK


def test_annotations_validated_and_logged(workspace, mm_tiff):
    p = workspace.create_project("P")
    ref = register_image(p, mm_tiff)
    n = len(p.operation_log)
    set_image_annotation(p, ref.id, "tags", ["ctrl", "best"])
    set_image_annotation(p, ref.id, "rating", 5)
    set_image_annotation(p, ref.id, "key_values", {"cell line": "MCF10A"})
    set_image_annotation(p, ref.id, "channel_stains", {0: "anti-ER (TxRed)"})
    assert len(p.operation_log) == n + 4
    assert p.images[ref.id] is ref and ref.rating == 5
    with pytest.raises(Exception):
        set_image_annotation(p, ref.id, "rating", 9)
    assert ref.rating == 5
    set_image_annotation(p, ref.id, "rating", 5)       # no-op: not logged
    assert len(p.operation_log) == n + 4
    assert [r.id for r in search_images(p, "mcf10a")] == [ref.id]
    assert [r.id for r in search_images(p, "txred")] == [ref.id]        # channel name / stain
    assert search_images(p, min_rating=5) and not search_images(p, tag="nope")


def test_save_reload_identical_and_atomic(workspace, mm_tiff, stk_tc):
    p = workspace.create_project("P")
    ds = create_dataset(p, "Ctrl")
    for f in (mm_tiff, stk_tc):
        _hashed(p, register_image(p, f, ds.id))
    workspace.save_project(p)
    q = workspace.load_project(p.project.id)
    assert q == p
    assert verify_log_chain(q).status == "ok"
    leftovers = [x.name for x in workspace.project_dir(p.project.id).iterdir()
                 if x.name != "project.json"]
    assert leftovers == []


def test_archive_is_soft(workspace):
    p = workspace.create_project("P")
    workspace.set_project_archived(p, True)
    assert workspace.list_projects() == []
    assert [m.id for m in workspace.list_projects(include_archived=True)] == [p.project.id]
    workspace.set_project_archived(p, False)
    assert len(workspace.list_projects()) == 1


def test_thumbnail(workspace, stk_tc):
    p = workspace.create_project("P")
    ref = register_image(p, stk_tc)
    out = write_thumbnail(workspace, p, ref)
    assert out is not None and out.is_file()


def test_integrity_report(workspace, mm_tiff, tmp_path):
    p = workspace.create_project("P")
    ref = _hashed(p, register_image(p, mm_tiff))
    ref.display.channels[0].gamma = 1.4
    report = build_integrity_report(workspace, p, verify_hashes=True)
    img = report["images"][0]
    assert report["log_chain"]["status"] == "ok"
    assert img["source_status"] == "ok" and img["hash_verified_now"]
    assert "nonlinear_gamma" in img["flags"] and img["nonlinear_channels"] == [0]
    assert img["calibration"]["pixel_size_x"] == pytest.approx(0.325)
    out = write_integrity_json(report, tmp_path / "integrity.json")
    assert out.read_text().startswith("{")
