# The `.pcarchive` format

A `.pcarchive` is a zip file (format version 1) that moves a project between
computers, adapted from PysternBlot's `.pbarchive`.

```text
pcarchive/manifest.json            format, level, app version, creation time,
                                   SHA-256 + size of every other member,
                                   SHA-256 of the project state, log-chain status
pcarchive/project/project.json     the project, image references relinked
pcarchive/project/images/…         complete: the original raw files
pcarchive/project/extracts/…       light: OME-TIFF extracts
pcarchive/exports/…                optional: exported figures + sidecars
pcarchive/reports/integrity.*      optional: integrity report (JSON + HTML)
```

## Levels

**Light** keeps only what the figures use: for each image, the time points,
Z planes (a projection's whole range) and visible channels its panels show,
cut to the region they display plus a 32-pixel margin, as uncompressed
OME-TIFF with calibration, channel names, the original metadata and the source
file's SHA-256. After import, panels open unchanged (same crops, shapes and
indices); the rest of the image does not exist, and exports say so ("original
not present"). **Complete** keeps every raw file, byte-exact.

## Import checks

Archives are treated as untrusted. Import refuses the whole archive — and
writes nothing — on: unsafe member names (absolute paths, `..`, backslashes),
symbolic links, duplicates, members missing from or not listed in the
manifest, a member larger than its limit (16 GB for images, 64 MB for text),
a total over 256 GB, a compression ratio over 1000:1 (images) or 200:1 (text),
not enough free disk space (members + 5 %), any SHA-256 mismatch, a project
state differing from its recorded hash, or a broken log chain. Every member is
verified in a temporary folder before the project is created — always as a
**new** project, never over an existing one.
