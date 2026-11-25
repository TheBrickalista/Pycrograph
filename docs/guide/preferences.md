# Preferences

The table lists the journal page presets: name, width, maximum height, dpi,
font family and size, minimum line width, publisher and status. The grey rows
are the built-in catalog (see {doc}`page-setup`): they are read-only and
always come from the catalog shipped with Pycrograph. To adapt one,
**Duplicate** it: the copy is yours, editable and listed under *Custom*.
**Add** makes a new preset, **Delete** removes one of yours.

The **default preset for new figures** is chosen like in Page setup:
publisher, then size (default: *Nature Portfolio › double column — 183 mm*).
Apply a preset to an existing figure with *Figures › Page setup…*.

Preferences are application settings stored in the workspace
(`preferences.json`, which keeps only your own presets); they are not
project data and are not logged. Applying a
preset to a figure is logged.
