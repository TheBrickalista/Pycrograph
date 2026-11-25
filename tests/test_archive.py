# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""`.pcarchive` (light / complete): round trips, untrusted-input refusals
(nothing written), source refusals, streaming, integrity section."""

from __future__ import annotations

import hashlib
import json
import zipfile

import numpy as np
import pytest
import tifffile

from pycrograph import archive as A
from pycrograph.figures import FigureEditor, create_figure
from pycrograph.insets import create_inset
from pycrograph.integrity import build_integrity_report, write_integrity_html
from pycrograph.logchain import verify_log_chain
from pycrograph.models import PanelLabel, ZProjection
from pycrograph.readers import open_image_ref
from pycrograph.readers.extract import EXTRACT_TAG, PlaneNotArchived
from pycrograph.render import PanelRenderer
from pycrograph.storage import create_dataset, register_image, set_image_hash, sha256_file
from synth import metamorph_xml, reference_like_plane, write_metamorph_tiff

REF_FIELDS_RELINKED = {"stored_relpath", "size_bytes", "mtime_ns", "extract"}


@pytest.fixture
def env(workspace, mm_tiff, stk_tc, stk_z, data_dir, tmp_path):
    unused = write_metamorph_tiff(data_dir / "unused.tif", [reference_like_plane(32, 32, seed=3)],
                                  [metamorph_xml(width=32, height=32)])
    p = workspace.create_project("Archive me")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z, unused)]
    for r in refs:
        set_image_hash(p, r.id, sha256_file(r.path))
    one, two, zs, _un = refs
    fig = create_figure(p, "Fig 1")
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    pa, pb, pc = ed.add_images([one, two, zs])
    pa.labels = [PanelLabel(text="[image]", position="top-left")]
    pb.t = 2
    pb.channels[1].visible = False
    pb.crop = pb.crop.model_copy(update={"x": 8, "y": 4, "w": 12, "h": 9})
    pc.z_projection = ZProjection(z_start=1, z_end=3)
    fig.panels.append(create_inset(fig, pa, one, (10, 10, 20, 16), "inset1"))
    workspace.save_project(p)
    out = tmp_path / "arch"
    out.mkdir()
    return p, refs, out


def projects_on_disk(workspace):
    return sorted(d.name for d in workspace.projects_dir.iterdir())


def renders(workspace, project):
    r = PanelRenderer(lambda ref: workspace.resolve_image_path(project, ref))
    try:
        return [r.render(project.images[q.image_id], q) for f in project.figures for q in f.panels]
    finally:
        r.close()


def assert_same_project(orig, imp, archived_ids):
    assert imp.project.id != orig.project.id
    assert [f.model_dump() for f in imp.figures] == [f.model_dump() for f in orig.figures]
    assert imp.datasets == orig.datasets
    for iid, ref in orig.images.items():
        a = ref.model_dump(exclude=REF_FIELDS_RELINKED)
        b = imp.images[iid].model_dump(exclude=REF_FIELDS_RELINKED)
        assert a == b, iid
        if iid not in archived_ids:
            assert imp.images[iid] == ref
    n = len(orig.operation_log)
    assert imp.operation_log[:n] == orig.operation_log[:n]
    assert [e.operation for e in imp.operation_log[n:]] == ["project.archive",
                                                            "project.import_archive"]
    assert verify_log_chain(imp).status == "ok"


def test_light_round_trip(workspace, env):
    p, (one, two, zs, unused), out = env
    before = renders(workspace, p)
    n_log = len(p.operation_log)
    res = A.export_archive(workspace, p, out / "light.pcarchive", "light")
    assert p.operation_log[-1].operation == "export.archive" and len(p.operation_log) == n_log + 1
    assert p.operation_log[-1].new_value["manifest_sha256"] == res.manifest_sha256
    man = A.read_manifest(res.path)
    assert man["level"] == "light" and man["images"][unused.id]["kind"] == "not_archived"
    assert man["project_sha256"] == res.project_sha256 and man["log_chain"]["status"] == "ok"
    with zipfile.ZipFile(res.path) as zf:
        for name, rec in man["members"].items():
            assert hashlib.sha256(zf.read(name)).hexdigest() == rec["sha256"]
            if name.endswith(".ome.tif"):
                assert zf.getinfo(name).compress_type == zipfile.ZIP_STORED
    snapshot = p.model_copy(deep=True)
    imp = A.import_archive(workspace, res.path)
    q = imp.project
    assert q.project.name == p.project.name
    assert_same_project(snapshot.model_copy(update={"operation_log": snapshot.operation_log[:-1]}),
                        q, {one.id, two.id, zs.id})
    assert q.operation_log[-1].new_value["manifest_sha256"] == res.manifest_sha256
    b = q.images[two.id]
    assert b.stored_relpath == f"extracts/{two.id}.ome.tif" and b.extract.t == [2]
    assert b.extract.c == [0] and b.sha256 == two.sha256           # original hash kept
    assert b.extract.source_sha256 == two.sha256
    assert q.images[zs.id].extract.z == [1, 2, 3]
    after = renders(workspace, q)
    assert len(after) == len(before) == 4
    assert all(np.array_equal(x, y) for x, y in zip(before, after))   # used region identical
    path = workspace.resolve_image_path(q, b)
    with open_image_ref(path, b) as src:
        with pytest.raises(PlaneNotArchived):
            src.get_plane(0, 0, 0)                                  # T1 was not shown
        assert src.get_plane(2, 0, 0).shape == (24, 32)
    with tifffile.TiffFile(path) as t:
        x = b.extract
        assert t.is_ome and len(t.pages) == len(x.t) * len(x.z) * len(x.c)
        assert t.pages[0].shape == (x.height, x.width) and t.pages[0].compression == 1
        info = json.loads(t.pages[0].tags[EXTRACT_TAG].value)
        assert info["source_sha256"] == two.sha256 and info["original_metadata"]["shape"]["X"] == 32
        assert two.sha256 in t.ome_metadata
    assert workspace.load_project(q.project.id) == q
    from pycrograph.storage import check_image
    assert check_image(workspace, q, b, verify_hash=True).value == "ok"


def test_complete_round_trip_and_extras(workspace, env):
    p, (one, two, zs, unused), out = env
    from pycrograph.export import ExportSettings, export_figure
    r = PanelRenderer(lambda ref: workspace.resolve_image_path(p, ref))
    export_figure(workspace, p, p.figures[0], r, out / "fig.svg", ExportSettings("svg"))
    r.close()
    before = renders(workspace, p)
    res = A.export_archive(workspace, p, out / "full.pcarchive", "complete",
                           include_exports=True, include_report=True)
    man = A.read_manifest(res.path)
    names = set(man["members"])
    assert {f"pcarchive/project/images/{i}.{ext}" for i, ext in
            ((one.id, "tif"), (two.id, "stk"), (zs.id, "stk"), (unused.id, "tif"))} <= names
    assert any(n.startswith("pcarchive/exports/") and n.endswith("fig.svg") for n in names)
    assert {"pcarchive/reports/integrity.json", "pcarchive/reports/integrity.html"} <= names
    snapshot = p.model_copy(deep=True)
    imp = A.import_archive(workspace, res.path)
    q = imp.project
    assert_same_project(snapshot.model_copy(update={"operation_log": snapshot.operation_log[:-1]}),
                        q, set(p.images))
    for ref in p.images.values():
        copied = workspace.resolve_image_path(q, q.images[ref.id])
        assert copied.read_bytes() == open(ref.path, "rb").read()            # byte-exact
        assert q.images[ref.id].extract is None
    after = renders(workspace, q)
    assert len(after) == len(before) == 4
    assert all(np.array_equal(x, y) for x, y in zip(before, after))
    extras = workspace.project_dir(q.project.id) / "archive"
    assert (extras / "reports" / "integrity.html").is_file()
    assert any(f.name.endswith("fig.svg") for f in (extras / "exports").iterdir())


# --------------------------------------------------------------- tampering
def rewrite(src, dst, edit=None, extra=None, manifest_edit=None, compress=None):
    """Copy an archive, editing members (bytes) / adding raw members."""
    with zipfile.ZipFile(src) as zf:
        members = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    man = json.loads(members["pcarchive/manifest.json"])
    if edit:
        edit(members)
    if manifest_edit:
        manifest_edit(man, members)
    members["pcarchive/manifest.json"] = json.dumps(man).encode()
    with zipfile.ZipFile(dst, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data, compress_type=compress or zipfile.ZIP_STORED)
        for info, data in extra or []:
            zf.writestr(info, data)
    return dst


@pytest.fixture
def light(workspace, env):
    p, refs, out = env
    return A.export_archive(workspace, p, out / "l.pcarchive", "light").path, out


def refused(workspace, path, match):
    before = projects_on_disk(workspace)
    with pytest.raises(A.ArchiveError, match=match):
        A.import_archive(workspace, path)
    assert projects_on_disk(workspace) == before                 # nothing written


def test_tampered_member_and_state(workspace, light):
    src, out = light

    def flip(members):
        name = next(n for n in members if n.endswith(".ome.tif"))
        b = bytearray(members[name])
        b[-1] ^= 0xFF
        members[name] = bytes(b)
    refused(workspace, rewrite(src, out / "t1.pcarchive", edit=flip), "tampered or corrupt")

    def edit_state(members):
        members["pcarchive/project/project.json"] = members["pcarchive/project/project.json"].replace(
            b'"Fig 1"', b'"Fig X"')

    def relist(man, members):                               # attacker updates the member list...
        data = members["pcarchive/project/project.json"]
        man["members"]["pcarchive/project/project.json"] = {
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    refused(workspace, rewrite(src, out / "t2.pcarchive", edit=edit_state, manifest_edit=relist),
            "project-state hash")                          # ...but not the project-state hash


@pytest.mark.parametrize("name", ["pcarchive/../evil.txt", "/etc/evil", "pcarchive\\evil",
                                  "pcarchive/./x", "other/x", "pcarchive/sp ace"])
def test_unsafe_member_names(workspace, light, name):
    src, out = light
    refused(workspace, rewrite(src, out / "s.pcarchive", extra=[(name, b"x")]), "unsafe")


def test_symlink_unlisted_and_duplicates(workspace, light):
    src, out = light
    link = zipfile.ZipInfo("pcarchive/project/link")
    link.external_attr = (0o120777 << 16)
    refused(workspace, rewrite(src, out / "l.pcarchive", extra=[(link, b"/etc/passwd")]),
            "symlink")
    refused(workspace, rewrite(src, out / "u.pcarchive", extra=[("pcarchive/project/x.bin", b"x")]),
            "disagree")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dup = rewrite(src, out / "d.pcarchive",
                      extra=[("pcarchive/project/project.json", b"{}")])
    refused(workspace, dup, "duplicate")


def test_size_limits_bomb_and_count(workspace, light, monkeypatch):
    src, out = light
    monkeypatch.setattr(A, "MAX_MEMBER_UNCOMPRESSED_BYTES", 1024)
    refused(workspace, src, "over the limit|limit")
    monkeypatch.undo()
    zeros = b"\0" * (8 * 1024 * 1024)

    def bomb(members):
        members["pcarchive/project/extracts/bomb.bin"] = zeros

    def listed(man, members):
        man["members"]["pcarchive/project/extracts/bomb.bin"] = {
            "sha256": hashlib.sha256(zeros).hexdigest(), "size": len(zeros)}
    b = rewrite(src, out / "b.pcarchive", edit=bomb, manifest_edit=listed,
                compress=zipfile.ZIP_DEFLATED)
    refused(workspace, b, "compression ratio")
    monkeypatch.setattr(A, "MAX_ARCHIVE_MEMBERS", 2)
    refused(workspace, src, "too many members")
    (out / "notzip.pcarchive").write_bytes(b"PK not really")
    refused(workspace, out / "notzip.pcarchive", "not a zip")


# ----------------------------------------------------------- export refusals
def test_missing_and_modified_sources(workspace, env):
    p, (one, two, zs, unused), out = env
    dest = out / "x.pcarchive"
    data = bytearray(open(two.path, "rb").read())
    data[-1] ^= 0xFF
    open(two.path, "wb").write(bytes(data))
    for level in ("light", "complete"):
        with pytest.raises(A.ArchiveError, match="MODIFIED"):
            A.export_archive(workspace, p, dest, level)
        assert not dest.exists() and not (out / "x.pcarchive.part").exists()
    res = A.export_archive(workspace, p, dest, "complete", allow_modified=True)
    assert any("MODIFIED" in w for w in res.warnings)
    assert "MODIFIED" in p.operation_log[-1].note
    assert A.read_manifest(dest)["images"][two.id]["modified_allowed"] is True
    imp = A.import_archive(workspace, dest)                     # imported, still flagged
    from pycrograph.storage import check_image
    assert check_image(workspace, imp.project, imp.project.images[two.id], verify_hash=True).value \
        == "modified"
    import os
    os.unlink(unused.path)
    with pytest.raises(A.ArchiveError, match="missing"):
        A.export_archive(workspace, p, out / "y.pcarchive", "complete", allow_modified=True)
    A.export_archive(workspace, p, out / "y.pcarchive", "light", allow_modified=True)  # unused: fine


def test_pending_hash_recorded_first(workspace, env):
    p, (one, two, zs, unused), out = env
    one.sha256 = None
    A.export_archive(workspace, p, out / "h.pcarchive", "light")
    assert one.sha256 == sha256_file(one.path)
    assert any(e.operation == "image.hash" and e.target_id == one.id for e in p.operation_log)


# ---------------------------------------------------------------- streaming
def test_streaming_and_cancel(workspace, env, monkeypatch):
    p, refs, out = env
    monkeypatch.setattr(A, "CHUNK", 4096)
    reads = []
    real_open = open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def read(self, n=-1):
            reads.append(n)
            return self.fh.read(n)

        def __getattr__(self, k):
            return getattr(self.fh, k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

    def spy_open(path, mode="r", *a, **k):
        fh = real_open(path, mode, *a, **k)
        return Spy(fh) if "r" in mode else fh
    monkeypatch.setattr(A, "open", spy_open, raising=False)
    res = A.export_archive(workspace, p, out / "s.pcarchive", "complete")
    assert reads and all(0 < n <= 4096 for n in reads)          # never a whole-file read
    A.import_archive(workspace, res.path)
    calls = [0]

    def cancel_soon():
        calls[0] += 1
        return calls[0] > 3
    before = projects_on_disk(workspace)
    with pytest.raises(A.ArchiveCancelled):
        A.export_archive(workspace, p, out / "c.pcarchive", "complete", cancelled=cancel_soon)
    assert not (out / "c.pcarchive").exists() and not (out / "c.pcarchive.part").exists()
    calls[0] = 0
    with pytest.raises(A.ArchiveCancelled):
        A.import_archive(workspace, res.path, cancelled=cancel_soon)
    assert projects_on_disk(workspace) == before


def test_integrity_archive_section(workspace, env):
    p, refs, out = env
    res = A.export_archive(workspace, p, out / "i.pcarchive", "light")
    rep = build_integrity_report(workspace, p)
    [a] = rep["archives"]
    assert (a["direction"], a["level"], a["status"]) == ("export", "light", "ok")
    assert a["manifest_sha256"] == res.manifest_sha256
    imp = A.import_archive(workspace, res.path)
    rep2 = build_integrity_report(workspace, imp.project)
    [b] = rep2["archives"]
    assert b["direction"] == "import" and b["members"] == res.n_members - 1
    html = write_integrity_html(rep2, out / "r.html").read_text()
    assert "Archives" in html and res.manifest_sha256 in html
    res.path.write_bytes(res.path.read_bytes() + b"x")
    assert build_integrity_report(workspace, p)["archives"][0]["status"] == "modified"


# ------------------------------------------- fix 1: provenance of extracts
def test_extract_provenance_everywhere(workspace, env, tmp_path):
    from pycrograph import consistency as C
    from pycrograph.export import ExportSettings, export_figure, export_sources
    p, (one, two, zs, unused), out = env
    res = A.export_archive(workspace, p, out / "lp.pcarchive", "light")
    q = A.import_archive(workspace, res.path).project
    fig = q.figures[0]
    r = PanelRenderer(lambda ref: workspace.resolve_image_path(q, ref))
    exp = export_figure(workspace, q, fig, r, out / "fromlight.pdf",
                        ExportSettings("pdf", info_page=True))
    side = json.loads(exp.sidecar.read_text())
    for rec in side["panels"]:
        ref = q.images[rec["image_id"]]
        assert "light_extract" in rec["flags"]
        im, ex = rec["image"], rec["extract"]
        assert im["original_present"] is False and im["sha256_verified_at_export"] is None
        assert im["extract_sha256_verified_at_export"] is True
        assert ex["extract_sha256"] == ref.extract.sha256 != ref.sha256
        assert ex["extract_sha256_verified_at_export"] is True
        assert ex["source_sha256"] == ref.sha256 and ex["source_sha256_verified"] is False
        assert ex["original_present"] is False and "not present" in ex["note"]
        assert ex["origin_px"] == {"x": ref.extract.origin_x, "y": ref.extract.origin_y}
        assert ex["archived_t"] == ref.extract.t and ex["archived_c"] == ref.extract.c
    assert any("original raw file not present" in w for w in side["warnings"])
    try:                                                        # info page says it in words
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        doc = QPdfDocument()
        doc.load(str(exp.files[0]))
        text = doc.getAllText(doc.pageCount() - 1).text()
        assert "LIGHT-ARCHIVE EXTRACT" in text and "not verified" in text
    except ImportError:                                         # pragma: no cover
        pass
    src = export_sources(workspace, q, fig, r, [fig.panels[0].id], out / "srcx")
    rec = json.loads(src.sidecar.read_text())["panels"][0]
    assert rec["light_extract"] is True and rec["extract"]["source_sha256_verified"] is False
    r.close()
    # Integrity report (JSON + HTML), even with hashes re-verified.
    rep = build_integrity_report(workspace, q, verify_hashes=True)
    im = next(i for i in rep["images"] if i["id"] == two.id)
    assert im["hash_verified_now"] is False and im["original_present"] is False
    assert im["extract"]["extract_sha256_verified_at_export"] is True
    assert "light_extract" in im["flags"] and rep["summary"]["n_light_extracts"] == 3
    html = write_integrity_html(rep, out / "x.html").read_text()
    assert "original raw file NOT present" in html and "recorded, not verified" in html
    # Consistency: one minor item per panel.
    issues = [i for i in C.check_figure(q, fig) if i.code == "light_extract"]
    assert len(issues) == len(fig.panels) and {i.level for i in issues} == {"minor"}
    assert "not the original raw file" in issues[0].message


# --------------------------------------------------- fix 2: JSON limits
def test_json_limits(workspace, light, env):
    src, out = light
    assert A.MAX_JSON_BYTES == 64 * 1024 * 1024 and A.MAX_JSON_COMPRESSION_RATIO == 200
    blob = b'{"pad": "' + b" " * (4 * 1024 * 1024) + b'"}'            # ~ 4000:1 deflated

    def add(members):
        members["pcarchive/reports/integrity.json"] = blob

    def listed(man, members):
        man["members"]["pcarchive/reports/integrity.json"] = {
            "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob)}
    bad = rewrite(src, out / "j.pcarchive", edit=add, manifest_edit=listed,
                  compress=zipfile.ZIP_DEFLATED)
    refused(workspace, bad, "compression ratio .* over 200:1")


def test_long_log_still_round_trips(workspace, env):
    """Our own archives pass our own checks: a very compressible project.json
    is stored, not deflated, when it would come near the JSON ratio cap."""
    from pycrograph.storage import log_operation
    p, refs, out = env
    for k in range(4000):
        log_operation(p, "display.set", target_type="image", target_id=refs[0].id,
                      field="display.channels[0].white", old_value=k, new_value=k + 1)
    res = A.export_archive(workspace, p, out / "log.pcarchive", "light")
    with zipfile.ZipFile(res.path) as zf:
        for i in zf.infolist():
            if i.filename.endswith(".json"):
                assert i.file_size / max(i.compress_size, 1) <= A.MAX_JSON_COMPRESSION_RATIO
    assert len(A.import_archive(workspace, res.path).project.operation_log) > 4000


# --------------------------------------------------- fix 3: disk space
def test_disk_space_refused(workspace, env, monkeypatch):
    p, refs, out = env
    res = A.export_archive(workspace, p, out / "c.pcarchive", "complete")
    total = sum(i.file_size for i in zipfile.ZipFile(res.path).infolist()
                if i.filename != "pcarchive/manifest.json")
    seen = []

    def free(path):
        seen.append(path)
        return int(total * 1.04)                               # < members + 5 %
    monkeypatch.setattr(A, "_free_bytes", free)
    refused(workspace, res.path, "not enough free disk space in the staging directory")
    # Different filesystems: the workspace is checked too.
    monkeypatch.setattr(A, "_same_device", lambda a, b: False)
    monkeypatch.setattr(A, "_free_bytes",
                        lambda path: 10 ** 15 if "pcarchive-in-" in str(path) else 1000)
    refused(workspace, res.path, "not enough free disk space in the workspace")
    monkeypatch.setattr(A, "_free_bytes", lambda path: int(total * 1.06))
    assert A.import_archive(workspace, res.path).project is not None   # members + 5 % is enough
