# Quickstart

1. **Run** `uv run python -m pycrograph`.
2. **New Project…** — a project starts with an *Unsorted* dataset.
   Create datasets for your conditions with **New Dataset…** (or right-click the tree).
3. **Import Images…** — select your TIFF / STK files (plain, ImageJ or
   MetaMorph). They are
   registered **by reference**: nothing is copied, the SHA-256 is computed in the
   background (⏳ in the tree until done). Right-click → *Copy into project* if you
   want a self-contained project for a small file.
4. **Select an image.** The centre view shows the composite (Shift+wheel to zoom,
   drag to pan, double-click or *Fit* to fit). T and Z sliders appear for stacks;
   the info line shows pixel size and physical field of view.
5. **Adjust each channel** in the right panel: visibility, LUT (named or custom
   colour), Black/White (drag the histogram gates, the sliders, or type values),
   *Auto* (0.1–99.9 percentiles), gamma (flagged when ≠ 1), invert, rotation/flips.
   A saturation badge reports clipped pixels in the current plane.
6. **Annotate** in the left panel: tags, rating (★), key–value pairs, and a
   stain/antibody per channel. Search filters the tree.
7. **External drive renamed?** Missing images show ⚠. Right-click → *Relink…*,
   point to one file: Pycrograph verifies it by SHA-256 and offers to fix every
   other image under the same old folder.
8. **Integrity Report…** writes a JSON report: source verification, log-chain
   status, display settings and flags.

Everything is saved automatically after each change.
