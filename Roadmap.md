# Roadmap

Indicative order; each step keeps the hard rules (figures only, raw pixels
untouched, local only, provenance).

1. **Session 1 — done (0.1.0):** scaffold, MetaMorph TIFF/STK readers, models,
   library with import by reference, per-channel display, hash-chained log.
2. **Canvas & panels:** page at journal width (presets as data: Nature 89 /
   183 mm, ≤ 170 mm high, ≥ 300 dpi, Arial ~8 pt), panels on a grid
   (rows = conditions, columns = channels), copy/paste display settings,
   lock levels/LUT across a row or column, replace image keeping settings.
   Designed as shared-core for PysternBlot's "structured figure composition".
3. **Auto-split / montage / insets:** one panel per channel + merge, T or Z
   montage, inset rectangles linked to zoom panels, max-intensity Z projection.
4. **Labels & scale bars** from metadata (channel names coloured like the
   channel, `t = 12 min`, Z, tags / key–values); consistent scale bars.
5. **Export:** SVG/PDF with vector text and native-resolution rasters,
   TIFF/PNG at a chosen dpi (resampling recorded), sidecar JSON + embedded
   metadata, optional info page.
6. **Consistency check** before export (levels/LUT differ within a compared
   row/column; source hash changed).
7. **Archives** (PysternBlot `.pbarchive` system): light (OME-TIFF of what the
   figure uses) and complete (raw files).
8. **MetaMorph `.nd`** sets, then `.czi` / `.nd2` / `.lif` via Java-free `bioio`.
9. **PyInstaller builds** (macOS 15+ Apple Silicon, Windows), Zenodo DOI.
10. **Pypirus hooks** (per-manuscript workspaces).

## Open decisions

- Background / flat-field correction: out of scope (default) vs a recorded,
  uniformly applied display operation.
- Priority of formats beyond MetaMorph.
- Exact shared-core boundaries with PysternBlot.
- Thumbnail grid view in the library (tree with thumbnails for now).
