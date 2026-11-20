# Projects

The Projects tab lists the projects of the workspace and the figures of the open
project.

- **New Project…** creates a project (with an *Unsorted* dataset).
- **Open** (or double-click a row) opens a project in the Library tab.
  **Home → Open Project** brings you here.
- **Archive / Restore** soft-hides a project (never deletes it); tick
  **Show archived** to see hidden ones.
- **Export archive…** writes the open project as a `.pcarchive`: *light* (only
  what the figures use, as OME-TIFF extracts) or *complete* (every raw file),
  optionally with the exported figures and an integrity report. See
  {doc}`../concepts/pcarchive-format`.
- **Import archive…** verifies a `.pcarchive` completely and imports it as a
  **new** project; nothing is written if any check fails.
- **Figures**: double-click a figure to open it; **New Figure…** creates one.

Long archive operations show a progress dialog and can be cancelled; a
cancelled export or import leaves nothing behind.
