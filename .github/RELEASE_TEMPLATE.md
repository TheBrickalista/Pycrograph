<!--
SHARED-CORE candidate (from PysternBlot: .github/RELEASE_TEMPLATE.md) — adapted.
Release-notes header for Pycrograph. Paste at the top of each GitHub release's
notes, replace every VERSION with the release number (e.g. 0.2.0 or 0.2.0b1,
without the "v"), then add the changes from CHANGELOG.md below it.
Every 0.x release is a beta: publish it with "Set as a pre-release" ticked
(also for betas / release candidates such as 0.2.0b1). The signed apps are
attached as usual, but publish.yml skips the PyPI upload for pre-releases.
Keep ONE of the two "Python (any OS)" rows: the "From source" row for 0.x
pre-releases, the `pip install pycrograph==VERSION` row from 1.0.0 (delete
the other). Zenodo archives every published release, 0.x pre-releases
included: check that the new version record appears on Zenodo.
The concept DOI (10.5281/zenodo.24404763, always the latest version) is
already in CITATION.cff, the README and the docs: nothing to change per
release. From 1.0.0, stable releases also go to PyPI.
Not read by any workflow.
-->

## ⬇ Download

| Platform | Download | Notes |
|---|---|---|
| **macOS** | [Pycrograph-vVERSION-macOS.zip](https://github.com/TheBrickalista/Pycrograph/releases/download/vVERSION/Pycrograph-vVERSION-macOS.zip) | macOS 15 or later, Apple Silicon. Signed and notarized: unzip, move to Applications, open. |
| **Windows** | [Pycrograph-vVERSION-Windows.exe](https://github.com/TheBrickalista/Pycrograph/releases/download/vVERSION/Pycrograph-vVERSION-Windows.exe) | Not yet code-signed: on first run, **More info → Run anyway**. |
| **Python (any OS)** | From source, see [Installation](https://pycrograph.readthedocs.io/en/latest/installation.html) | Python 3.11 or later; also for Intel Macs (macOS 15 or later). `pip install pycrograph` from 1.0.0. |
| **Python (any OS)** | `pip install pycrograph==VERSION` | Python 3.11 or later; also for Intel Macs (macOS 15 or later). |

Full documentation: https://pycrograph.readthedocs.io

---
