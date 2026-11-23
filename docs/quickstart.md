# Quickstart

1. **Home → New Project.** A project starts with an *Unsorted* dataset.
2. **Library → Import Images…** Select your TIFF / STK files (plain, ImageJ
   or MetaMorph). They are registered **by reference**: nothing is copied;
   the SHA-256 is computed in the background (⏳ in the tree until done).
3. **Adjust the image's defaults** in the Library: per channel visibility, LUT,
   Black/White (histogram gates, sliders or typed values), *Auto*, gamma
   (flagged when ≠ 1), invert; T / Z; rotation and flips.
4. **Add to Figure** (toolbar or right-click): the image becomes a panel on a
   journal-width page in the **Figures** tab, with a copy of those settings.
5. **Lay out** the figure: drag panels (alignment guides, grid snap), resize
   with the corner handle (the crop grows, the image is never stretched), use
   *Align*, *Grid*, *Size*, *Align magnification*, or the *Auto* layouts
   (split channels, montage, condition grid).
6. **Edit the selected panels** in the inspector: *Preview* (viewport, T/Z,
   channels, locks), *Labels* (text with tokens such as `[channels]` or
   `[time:min]`, scale bar, border), *Insets & Shapes*.
7. **Check consistency**, then **Export…** to PDF / SVG / TIFF / PNG. Each export
   writes a `.provenance.json` next to the file and one entry in the
   hash-chained log.
8. **Projects → Export archive…** to hand the project over (light or complete).

Everything is saved automatically after each change; every change can be
undone in the Figures tab.
