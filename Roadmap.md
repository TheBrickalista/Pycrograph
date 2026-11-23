# Roadmap

Indicative order; each step keeps the hard rules (figures only, raw pixels
untouched, local only, provenance).

**Release plan.**

- **0.x betas** (from 0.2.0): GitHub pre-releases with the signed macOS app
  and the Windows executable, each archived on Zenodo with a DOI (a dated,
  citable record of every release). No PyPI upload.
- **1.0.0**: first stable release, archived on Zenodo as before and also
  published on PyPI.

1. **Session 1 — done (0.1.0):** scaffold, MetaMorph TIFF/STK readers, models,
   library with import by reference, per-channel display, hash-chained log.
2. **Figure editor — done (0.2.0, first public beta), items 3–8 below:** canvas, layout tools, labels and
   scale bars, insets and shapes, consistency check, provenance-tracked export,
   `.pcarchive` archives.
3. **Canvas & panels:** page at journal width (presets as data: Nature 89 /
   183 mm, ≤ 170 mm high, ≥ 300 dpi, Arial ~8 pt), panels on a grid
   (rows = conditions, columns = channels), copy/paste display settings,
   lock levels/LUT across a row or column, replace image keeping settings.
   Designed as shared-core for PysternBlot's "structured figure composition".
4. **Auto-split / montage / insets:** one panel per channel + merge, T or Z
   montage, inset rectangles linked to zoom panels, max-intensity Z projection.
5. **Labels & scale bars** from metadata (channel names coloured like the
   channel, `t = 12 min`, Z, tags / key–values); consistent scale bars.
6. **Export:** SVG/PDF with vector text and native-resolution rasters,
   TIFF/PNG at a chosen dpi (resampling recorded), sidecar JSON + embedded
   metadata, optional info page.
7. **Consistency check** before export (levels/LUT differ within a compared
   row/column; source hash changed).
8. **Archives** (PysternBlot `.pbarchive` system): light (OME-TIFF of what the
   figure uses) and complete (raw files).
9. **MetaMorph `.nd`** sets, then `.czi` / `.nd2` / `.lif` via Java-free `bioio`.
10. **PyInstaller builds** (macOS 15+ Apple Silicon, Windows) and Zenodo
    archiving — done from 0.2.0 (GitHub pre-releases). **1.0.0 stable:** also
    on PyPI.
11. **Pypirus hooks** (per-manuscript workspaces).

## Open decisions

- Background / flat-field correction: out of scope (default) vs a recorded,
  uniformly applied display operation.
- Order in which further formats are added.
- Exact shared-core boundaries with PysternBlot.
- Thumbnail grid view in the library (tree with thumbnails for now).
