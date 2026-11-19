# Display pipeline

Display settings are re-applied to the raw pixels at every render; nothing is
written back to the image.

1. **Read** one plane per visible channel at the panel's T / Z (or the maximum
   over a Z range for a projection). Planes are read lazily, one at a time.
2. **Crop** by array slicing: the panel's crop is a rectangle in raw-pixel
   coordinates; only the part on the image is read.
3. **Per channel**: window `[black, white]` → gamma (`x ** (1/gamma)`) →
   invert → colour LUT; channels are summed (additive composite) and quantised
   once to 8-bit RGB.
4. **Orientation**: flips, then 90° rotations — index operations, no
   resampling.

## Viewport = crop

A panel shows its crop at one uniform scale, so the image is never stretched;
zoom % (100 % = whole image fitted) and pan are derived from the crop.
Resizing a panel extends the crop at constant scale; where the crop runs past
the image the page background shows.

## Resolution and resampling

The canvas shows panels decimated to at most 1024 px per side (pixels dropped,
not interpolated). PDF and SVG exports embed each panel's pixels at native
crop resolution. TIFF and PNG are rendered at the chosen dpi with
nearest-neighbour (default) or smooth (bilinear) resampling, recorded in the
provenance. The *effective resolution* (crop pixels per inch on the page) is
shown in the Info tab and checked against the page preset's dpi.
