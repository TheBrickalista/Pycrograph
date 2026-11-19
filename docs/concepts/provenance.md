# Provenance

Pycrograph follows PysternBlot's rules: the raw pixels are never modified, and
everything needed to say how a figure was made is recorded and verifiable.

## Images by reference, verified by SHA-256

Importing an image records its path, size, date, reader and metadata; the
SHA-256 is computed in the background. *Check Sources* compares size and date;
a changed file is re-hashed. A relink is accepted only if the new file has the
recorded SHA-256. *Copy into project* makes a byte-exact copy verified after
copying.

## The operation log

Every change to a project is one entry per gesture (a slider drag is one
entry, not one per tick; a gesture on several panels is one entry per panel)
in a **hash-chained** log saved with the project: editing or removing an entry
breaks the chain, which the integrity report shows.

## Exports

Each export writes a sidecar `<file>.provenance.json`: app version, export
settings (format, dpi, resampling), page setup, log-chain status and, per
panel, the source path and SHA-256 (re-verified at export), reader, T / Z or
projection, the crop as rendered (pixel bounds clipped to the image),
orientation, every channel setting, flags (non-linear gamma, 8-bit source,
upsampled, light-archive extract), labels as resolved text, the scale bar's
length and the pixel size it is based on, insets and shapes. The sidecar's
SHA-256 is embedded in the exported file (SVG `<metadata>`, PDF info and XMP,
TIFF description, PNG text) and logged with the SHA-256 of every written
file. Missing or modified sources refuse the export unless explicitly allowed
(then logged).

## Integrity report

*Integrity Report…* writes JSON and HTML: per image the source status and
hash, display settings and flags; every export re-checked against its logged
SHA-256; archives made from or imported into the project; the log-chain
status.
