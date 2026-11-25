# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
"""Export (§4) and provenance: native-resolution lossless PDF rasters, text
as text, raster dpi / resampling / geometry, multi-page, sidecar contents,
embedded metadata, source verification, log entries, OME-TIFF sources,
integrity report (JSON + HTML)."""

from __future__ import annotations

import base64
import io
import json
import re
import zlib

import numpy as np
import pytest
import tifffile

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pycrograph import viewport as V  # noqa: E402
from pycrograph.export import ExportRefused, ExportSettings, export_figure, export_sources  # noqa: E402
from pycrograph.figures import FigureEditor, create_figure  # noqa: E402
from pycrograph.insets import create_inset  # noqa: E402
from pycrograph.integrity import build_integrity_report, write_integrity_html  # noqa: E402
from pycrograph.logchain import verify_log_chain  # noqa: E402
from pycrograph.models import PanelLabel, Shape, ZProjection  # noqa: E402
from pycrograph.readers import open_image  # noqa: E402
from pycrograph.render import PanelRenderer, crop_bounds  # noqa: E402
from pycrograph.scalebar import bar_geometry  # noqa: E402
from pycrograph.storage import (  # noqa: E402
    create_dataset, register_image, set_image_hash, sha256_file,
)
from pycrograph.ui.figure_canvas import FigureScene  # noqa: E402
from pycrograph.ui.scene_builder import export_scene, overlay_builder, panel_visual  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(app, workspace, mm_tiff, stk_tc, stk_z, tmp_path):
    p = workspace.create_project("Export")
    ds = create_dataset(p, "A")
    refs = [register_image(p, f, ds.id) for f in (mm_tiff, stk_tc, stk_z)]
    for r in refs:
        set_image_hash(p, r.id, sha256_file(r.path))
    one, two, zs = refs
    fig = create_figure(p, "Fig 1")
    fig.legend = "Figure 1. Nuclei and ER."
    ed = FigureEditor(p, fig, lambda: workspace.save_project(p))
    pa, pb, pc = ed.add_images([one, two, zs])
    pa.labels = [PanelLabel(text="[image] t=[time]", position="top-left"),
                 PanelLabel(text="[channels]", position="top")]
    pa.scale_bar.visible, pa.scale_bar.length = True, 5.2
    pa.shapes = [Shape(id="s1", kind="arrow", points=[(5, 5), (30, 20)])]
    s = two.metadata.shape
    V.set_rotation(pb, s.X, s.Y, 90)
    V.zoom_by(pb, s.X, s.Y, 0.7)
    V.pan(pb, s.X, s.Y, -6, 0)                           # crop runs off the image
    pb.channels[1].gamma = 1.3
    pc.z_projection = ZProjection(z_start=0, z_end=4)
    inset = create_inset(fig, pa, one, (10, 10, 20, 16), "inset1")
    fig.panels.append(inset)
    renderer = PanelRenderer(lambda r: workspace.resolve_image_path(p, r))
    yield p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), tmp_path / "out"
    renderer.close()


def native_sizes(p, fig, renderer):
    """Oriented native raster size of each panel (what must be embedded)."""
    out = {}
    for q in fig.panels:
        rgb = renderer.render(p.images[q.image_id], q)
        out[q.id] = (rgb.shape[1], rgb.shape[0])
    return out


def pdf_images(data: bytes):
    """[(width, height, filter, decoded bytes)] of every image XObject."""
    out = []
    for m in re.finditer(rb"(\d+) 0 obj\s*<<(.*?)>>\s*stream\r?\n", data, re.S):
        d = m.group(2)
        if b"/Subtype /Image" not in d:
            continue
        w = int(re.search(rb"/Width (\d+)", d).group(1))
        h = int(re.search(rb"/Height (\d+)", d).group(1))
        filt = (re.search(rb"/Filter /(\w+)", d) or [None, b"none"])[1]
        end = data.index(b"endstream", m.end())
        raw = data[m.end():end].rstrip(b"\r\n")
        out.append((w, h, filt, zlib.decompress(raw) if filt == b"FlateDecode" else raw, d))
    return out


def test_pdf_lossless_native_text_metadata(env, workspace):
    p, fig, renderer, refs, panels, out = env
    res = export_figure(workspace, p, fig, renderer, out / "fig.pdf",
                        ExportSettings("pdf", info_page=True))
    data = res.files[0].read_bytes()
    assert b"DCTDecode" not in data and b"JPXDecode" not in data
    imgs = [i for i in pdf_images(data) if b"/DeviceRGB" in i[4]]
    sizes = {(w, h) for w, h, *_ in imgs}
    native = native_sizes(p, fig, renderer)
    assert set(native.values()) <= sizes                      # every panel at native size
    assert all(f == b"FlateDecode" for _w, _h, f, *_ in imgs)
    # Lossless: the embedded pixels ARE the rendered pixels.
    pa = panels[0]
    rgb = renderer.render(p.images[pa.image_id], pa)
    assert any(dec == rgb.tobytes() for w, h, _f, dec, _d in imgs if (w, h) == native[pa.id])
    pb = panels[1]
    b = crop_bounds(pb.crop, 32, 24)
    assert native[pb.id] == (b[3] - b[1], b[2] - b[0])        # clipped crop, rotated 90°
    assert b"/FontFile2" in data or b"/FontFile3" in data     # fonts embedded
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 2   # figure + info page
    assert res.sidecar_sha256.encode() in data                # XMP
    assert b"/Title" in data


def test_svg_text_native_png_metadata(env, workspace):
    p, fig, renderer, refs, panels, out = env
    res = export_figure(workspace, p, fig, renderer, out / "fig.svg", ExportSettings("svg"))
    svg = res.files[0].read_text()
    assert 'width="183mm"' in svg and 'height="170mm"' in svg
    assert "<text" in svg and "Nuc_ER_ER" in svg and "font-family" in svg
    assert '<metadata id="pycrograph-provenance">' in svg and res.sidecar_sha256 in svg
    pngs = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=\s]+)", svg)
    sizes = set()
    for b64 in pngs:
        img = QImage.fromData(base64.b64decode("".join(b64.split())), "PNG")
        sizes.add((img.width(), img.height()))
    assert set(native_sizes(p, fig, renderer).values()) <= sizes


def test_raster_dpi_geometry_and_resampling(env, workspace):
    p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), out = env
    res = export_figure(workspace, p, fig, renderer, out / "fig.tiff", ExportSettings("tiff", dpi=600))
    with tifffile.TiffFile(res.files[0]) as t:
        page = t.pages[0]
        arr = page.asarray()
        assert page.tags["XResolution"].value == (600, 1)
        assert json.loads(page.description)["provenance_sha256"] == res.sidecar_sha256
    assert arr.shape == (round(170 / 25.4 * 600), round(183 / 25.4 * 600), 3)
    # Geometry: pa's image occupies exactly its image_rect at 600 dpi.
    s = one.metadata.shape
    x, y, w, h = V.image_rect_mm(pa, s.X, s.Y, crop_bounds(pa.crop, s.X, s.Y))
    k = 600 / 25.4
    native = renderer.render(one, pa)
    block = arr[int((pa.y_mm + y + h / 2) * k), int((pa.x_mm + x + 2) * k):int((pa.x_mm + x + w - 2) * k)]
    native_colours = {tuple(c) for c in native.reshape(-1, 3)}
    # nearest neighbour: only colours of the native raster (labels/shapes aside, away from them)
    assert {tuple(c) for c in block[: block.shape[0] // 3]} <= native_colours | {(255, 255, 255)}
    # pb's crop runs off the image: page background on its left side
    pb_left = arr[int((pb.y_mm + pb.height_mm / 2) * k), int((pb.x_mm + 0.5) * k)]
    assert tuple(pb_left) == (255, 255, 255)
    smooth = export_figure(workspace, p, fig, renderer, out / "fig_smooth.png",
                           ExportSettings("png", dpi=600, resampling="smooth"))
    img = QImage(str(smooth.files[0]))
    assert round(img.dotsPerMeterX() * 0.0254) == 600
    assert json.loads(img.text("pycrograph:provenance"))["provenance_sha256"] == smooth.sidecar_sha256
    side = json.loads(smooth.sidecar.read_text())
    assert side["export"]["resampling"] == "smooth" and "bilinear" in side["export"]["resampling_method"]
    assert json.loads(res.sidecar.read_text())["export"]["resampling_method"] == "nearest-neighbour"


def test_export_scene_matches_canvas(env):
    p, fig, renderer, refs, panels, out = env
    canvas = FigureScene()
    canvas.overlay_builder = overlay_builder(p, lambda: fig)
    canvas.populate(fig, lambda q: panel_visual(p, renderer, q, 1024))
    exp = export_scene(p, fig, renderer)
    assert exp.decorations is False and exp.smooth_images is False
    a = {it.panel_id: it for it in canvas.panel_items()}
    for it in exp.panel_items():
        c = a[it.panel_id]
        assert (it.pos(), it.rect(), it.image_rect) == (c.pos(), c.rect(), c.image_rect)
        kinds_e = sorted(str(k.data(1)) for k in it.childItems())
        kinds_c = sorted(str(k.data(1)) for k in c.childItems())
        assert kinds_e == kinds_c
    assert all(pg.pen().style() == Qt.NoPen for pg in exp.page_items)   # page border off


def test_multipage(env, workspace):
    p, fig, renderer, refs, (pa, pb, pc, inset), out = env
    fig.page.n_pages = 2
    pc.page = 1
    res = export_figure(workspace, p, fig, renderer, out / "two.svg", ExportSettings("svg"))
    assert [f.name for f in res.files] == ["two_p1.svg", "two_p2.svg"]
    pdf = export_figure(workspace, p, fig, renderer, out / "two.pdf", ExportSettings("pdf"))
    data = pdf.files[0].read_bytes()
    assert len(re.findall(rb"/Type /Page[\s/>]", data)) == 2


def test_sidecar_contents_and_log(env, workspace):
    p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), out = env
    n = len(p.operation_log)
    res = export_figure(workspace, p, fig, renderer, out / "fig.png", ExportSettings("png"),
                        overridden=[{"level": "major", "code": "tz", "rule": "tz",
                                     "message": "row 1: x", "panel_ids": [pa.id]}])
    side = json.loads(res.sidecar.read_text())
    assert res.sidecar.name == "fig.png.provenance.json"
    assert side["report_type"] == "pycrograph.figure_export" and side["log_chain"]["status"] == "ok"
    assert side["export"]["dpi"] == 300 and side["export"]["files"] == ["fig.png"]
    recs = {r["id"]: r for r in side["panels"]}
    a, b, c, i = (recs[q.id] for q in (pa, pb, pc, inset))
    assert a["image"]["sha256_recorded"] == one.sha256 and a["image"]["sha256_verified_at_export"]
    assert a["image"]["reader"] == one.reader
    assert [lb["text"] for lb in a["labels"]] == ["Nuc_ER_ER t=?", "5 TxRed"]
    assert a["labels"][0]["missing"] == ["[time]"]
    sb = a["scale_bar"]
    assert sb["pixel_size_basis"] == {"axis": "x", "pixel_size": 0.325, "unit": "um",
                                      "source": one.metadata.calibration.source}
    assert sb["length_on_page_mm"] == pytest.approx(5.2 / 0.325 / (64 / 40), abs=1e-3)
    assert a["shapes"][0]["kind"] == "arrow" and a["insets"] == [inset.id]
    cr = b["crop_rendered_px"]
    assert min(cr["x0"], cr["y0"]) >= 0 and b["crop_requested"]["x"] < 0     # clipped vs requested
    assert b["orientation"]["rotation_deg"] == 90 and "nonlinear_gamma" in b["flags"]
    assert c["z_projection"] == {"method": "max", "z_start": 0, "z_end": 4}
    assert i["inset"]["parent_id"] == pa.id and i["inset"]["rectangle_raw_px"]["width"] == 20
    assert i["inset"]["rectangle_on_parent_mm"]["width"] > 0
    assert side["consistency_warnings_overridden"][0]["code"] == "tz"
    assert side["consistency_warnings_overridden"][0]["level"] == "major"
    assert side["report_version"] == 2                          # level instead of severity
    [e] = p.operation_log[n:]
    assert e.operation == "export.figure" and e.target_id == fig.id
    assert e.new_value["files"][0]["sha256"] == sha256_file(res.files[0])
    assert e.new_value["sidecar"]["sha256"] == sha256_file(res.sidecar) == res.sidecar_sha256
    assert e.new_value["consistency_warnings"][0]["code"] == "tz" and "despite 1" in e.note
    assert verify_log_chain(p).status == "ok"


def test_source_verification(env, workspace):
    p, fig, renderer, (one, two, zs), panels, out = env
    path = workspace.resolve_image_path(p, two)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF                                         # same size, different content
    path.write_bytes(bytes(data))
    with pytest.raises(ExportRefused, match="MODIFIED"):
        export_figure(workspace, p, fig, renderer, out / "x.pdf", ExportSettings("pdf"))
    res = export_figure(workspace, p, fig, renderer, out / "x.pdf",
                        ExportSettings("pdf", allow_modified=True))
    assert any("MODIFIED" in w for w in res.warnings)
    assert "MODIFIED" in p.operation_log[-1].note
    zs.sha256 = None                                         # never hashed
    res = export_figure(workspace, p, fig, renderer, out / "y.pdf",
                        ExportSettings("pdf", allow_modified=True))
    side = json.loads(res.sidecar.read_text())
    rec = next(r for r in side["panels"] if r["image_id"] == zs.id)
    assert rec["image"]["sha256_at_export"] == sha256_file(zs.path)
    workspace.resolve_image_path(p, one).unlink()
    with pytest.raises(ExportRefused, match="missing"):
        export_figure(workspace, p, fig, renderer, out / "z.pdf",
                      ExportSettings("pdf", allow_modified=True))


def test_source_data_ome_tiff(env, workspace):
    p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), out = env
    res = export_sources(workspace, p, fig, renderer, [pa.id, pb.id, pc.id], out / "src", "fig1")
    assert [f.name for f in res.files] == ["fig1_panel01_Nuc_ER_ER.ome.tif",
                                           "fig1_panel02_timelapse 2ch.ome.tif",
                                           "fig1_panel03_zstack.ome.tif"]
    with tifffile.TiffFile(res.files[1]) as t:
        assert t.is_ome and t.series[0].axes == "CYX" and t.pages[0].compression == 1
        data = t.series[0].asarray()
        assert 'PhysicalSizeX="0.325"' in t.ome_metadata
    x0, y0, x1, y1 = crop_bounds(pb.crop, 32, 24)
    with open_image(two.path) as src:
        expected = np.stack([np.array(src.get_plane(pb.t, 0, c)[y0:y1, x0:x1]) for c in (0, 1)])
    assert data.dtype == np.uint16 and np.array_equal(data, expected)   # raw, unrotated
    with tifffile.TiffFile(res.files[2]) as t:
        proj = t.series[0].asarray()
        proj = proj[None] if proj.ndim == 2 else proj         # tifffile squeezes C = 1
    with open_image(zs.path) as src:
        mx = np.max(np.stack([np.array(src.get_plane(0, z, 0)) for z in range(5)]), axis=0)
    b = crop_bounds(pc.crop, 16, 16)
    assert np.array_equal(proj[0], mx[b[1]:b[3], b[0]:b[2]])
    side = json.loads(res.sidecar.read_text())
    assert side["report_type"] == "pycrograph.source_export"
    assert side["panels"][1]["orientation_of_panel"]["rotation_deg"] == 90
    assert p.operation_log[-1].operation == "export.sources"


def test_integrity_report_lists_exports(env, workspace):
    p, fig, renderer, refs, panels, out = env
    res = export_figure(workspace, p, fig, renderer, out / "fig.svg", ExportSettings("svg"))
    report = build_integrity_report(workspace, p)
    [x] = report["exports"]
    assert {f["status"] for f in x["files"]} == {"ok"}
    res.files[0].write_text(res.files[0].read_text() + "<!-- edited -->")
    report = build_integrity_report(workspace, p)
    assert [f["status"] for f in report["exports"][0]["files"]][0] == "modified"
    assert report["summary"]["n_export_files_not_ok"] == 1
    html = write_integrity_html(report, out / "integrity.html").read_text()
    assert "Pycrograph integrity report" in html and "fig.svg" in html and "modified" in html
    assert io.StringIO(html).read().startswith("<!doctype html>")


def _svg_texts(svg: str) -> list[str]:
    """Text content of every <text> element (QSvgGenerator writes one per run)."""
    return [re.sub(r"<[^>]+>", "", t).strip()
            for t in re.findall(r"<text\b[^>]*>(.*?)</text>", svg, flags=re.S)]


def test_hidden_inset_number_not_exported(env, workspace):
    p, fig, renderer, refs, (pa, pb, pc, inset), out = env
    shown = export_figure(workspace, p, fig, renderer, out / "shown.svg", ExportSettings("svg"))
    texts = _svg_texts(shown.files[0].read_text())
    assert texts.count("1") == 2                       # on the inset + next to its rectangle
    side = json.loads(shown.sidecar.read_text())
    rec = next(r for r in side["panels"] if r["id"] == inset.id)
    assert (rec["inset"]["number"], rec["inset"]["number_shown"]) == (1, True)

    inset.inset_style.show_number = False
    hidden = export_figure(workspace, p, fig, renderer, out / "hidden.svg", ExportSettings("svg"))
    svg = hidden.files[0].read_text()
    assert "1" not in _svg_texts(svg)                  # neither number is drawn
    assert "Nuc_ER_ER" in svg                           # other text still there
    assert "#ffff00" in svg.lower()                     # rectangle / border colour kept
    side = json.loads(hidden.sidecar.read_text())
    rec = next(r for r in side["panels"] if r["id"] == inset.id)
    assert (rec["inset"]["number"], rec["inset"]["number_shown"]) == (1, False)
    # PDF: exactly the two number text blocks disappear.
    pdf_hidden = export_figure(workspace, p, fig, renderer, out / "hidden.pdf", ExportSettings("pdf"))
    inset.inset_style.show_number = True
    pdf_shown = export_figure(workspace, p, fig, renderer, out / "shown.pdf", ExportSettings("pdf"))
    assert _pdf_text_blocks(pdf_shown.files[0].read_bytes()) - \
        _pdf_text_blocks(pdf_hidden.files[0].read_bytes()) == 2


def _pdf_text_blocks(data: bytes) -> int:
    """Number of BT…ET text objects in the PDF's (Flate) content streams."""
    n = 0
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.S):
        try:
            body = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        n += len(re.findall(rb"(?:^|\s)BT\s", body))
    return n


def test_inset_of_parent_with_scale_bar_exports_no_bar(env, workspace):
    p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), out = env
    assert pa.scale_bar.visible and inset.inset_of == pa.id
    assert inset.scale_bar.visible is False and inset.scale_bar.length == pa.scale_bar.length
    text = bar_geometry(pa, one).text
    res = export_figure(workspace, p, fig, renderer, out / "bars.svg", ExportSettings("svg"))
    assert _svg_texts(res.files[0].read_text()).count(text) == 1      # the parent's bar only
    side = json.loads(res.sidecar.read_text())
    rec = next(r for r in side["panels"] if r["id"] == inset.id)
    assert rec["scale_bar"]["visible"] is False and "text" not in rec["scale_bar"]
    # Ticked on later: the parent's length and style, at the inset's magnification.
    inset.scale_bar.visible = True
    res = export_figure(workspace, p, fig, renderer, out / "bars2.svg", ExportSettings("svg"))
    assert _svg_texts(res.files[0].read_text()).count(text) == 2


# ------------------------------------------------------------ smooth resampling and insets
@pytest.mark.parametrize("fmt,resampling,warned", [("png", "smooth", True), ("tiff", "smooth", True),
                                                   ("png", "nearest", False),
                                                   ("pdf", "smooth", False), ("svg", "smooth", False)])
def test_smooth_resampling_warns_for_low_res_insets(env, workspace, fmt, resampling, warned):
    from pycrograph.consistency import SMOOTH_INSET_WARNING, effective_ppi, insets_below_dpi
    p, fig, renderer, (one, two, zs), (pa, pb, pc, inset), out = env
    assert effective_ppi(inset, one) < fig.page.preset.dpi and insets_below_dpi(p, fig) == [inset]
    res = export_figure(workspace, p, fig, renderer, out / f"f.{fmt}",
                        ExportSettings(fmt, resampling=resampling))
    side = json.loads(res.sidecar.read_text())
    assert (SMOOTH_INSET_WARNING in res.warnings) is warned
    assert (SMOOTH_INSET_WARNING in side["warnings"]) is warned
    assert (SMOOTH_INSET_WARNING in (p.operation_log[-1].note or "")) is warned
    assert res.files[0].is_file()                                # never blocks


def test_no_smooth_warning_without_low_res_inset(env, workspace):
    from pycrograph.consistency import SMOOTH_INSET_WARNING
    p, fig, renderer, _refs, (pa, pb, pc, inset), out = env
    fig.panels.remove(inset)
    res = export_figure(workspace, p, fig, renderer, out / "f.png", ExportSettings("png", resampling="smooth"))
    assert SMOOTH_INSET_WARNING not in res.warnings
