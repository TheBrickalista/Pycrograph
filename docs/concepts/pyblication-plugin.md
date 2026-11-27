# Pyblication plugin and edit locks

[Pyblication](https://github.com/TheBrickalista/Pyblication) tracks the figures of a
manuscript. When you put a figure exported by Pycrograph into a Pyblication
workspace, Pyblication asks Pycrograph's plugin which project made it and which
files it was made from. It then keeps a copy, or a fingerprint, of those files
with every revision of the panel.

Pycrograph works the same without Pyblication: it never imports Pyblication and
has no dependency on it. The plugin is a plain module, `pycrograph.plugin`,
declared as an entry point (plugin protocol version 1):

```toml
[project.entry-points."pyblication.plugins"]
pycrograph = "pycrograph.plugin:describe"
```

## What the plugin does

**`describe()`** names the plugin (`pycrograph`), the app (Pycrograph), its version,
the protocol (1) and the export extensions it can identify: `.pdf`, `.svg`, `.png`,
`.tif` and `.tiff`.

**`identify(export)`** hashes the file (SHA-256) and looks for it in the operation
log of every project in the workspace (`~/.pycrograph`, or `PYCROGRAPH_WORKSPACE`).
Each figure export writes one `export.figure` entry that lists every file it wrote
with its SHA-256, so each page of a multi-page SVG, PNG or TIFF export is found.
If the same file is logged in several projects, the most recent entry wins and
the other projects are listed in `also_logged_in`. A file that is not a
Pycrograph export of this workspace returns nothing.

The plugin reads files and nothing else. It writes nowhere (not in the workspace,
not next to the raw images) and never needs the edit lock below. It does not
import Qt, so Pyblication's command-line tools can load it.

**`open_project(id, path)`** starts Pycrograph on that project in a separate
process: `python -m pycrograph --project <id>`. The workspace is the one that
holds `path`. Launched without `--project`, Pycrograph starts as before and opens
the most recent project.

## Files reported for an export

| Name in the tree | Role | File | SHA-256 given |
|---|---|---|---|
| `project.json` | project | `projects/<id>/project.json` | no (it changes with every edit) |
| `sidecar/<export>.provenance.json` | sidecar | the export's provenance sidecar, if found | the one in the log entry |
| `images/<image id>/<file name>` | raw | each image the export shows | the recorded one, if any |

**The sidecar** is looked for where the export wrote it, then next to the file
Pyblication is identifying, for example `panels/p1.pdf.provenance.json`. It is
used only if its SHA-256 is the one logged with the export.

**The raw images** are the ones the export was made from, read from the
sidecar's panel records (`raw_members_from: "sidecar"`). Panels added to or
removed from the figure afterwards don't change them. Without a valid sidecar,
the figure's current panels are used instead, and the provenance says so
(`raw_members_from: "current_figure"`). Each path is resolved as Pycrograph reads
it:

- a file copied into the project, or a light-archive extract: inside the project
  folder (`images/…`, `extracts/<image id>.ome.tif`);
- otherwise, the path the file was imported from.

A raw image that has gone missing is still listed, and Pyblication reports it.
Its SHA-256 is given only when Pycrograph has recorded one. For a light-archive
extract that is the extract's own hash, because the original is not there.
Pyblication hashes every file again. If a raw file has changed since it was
imported, it reports the mismatch. That is intended: the figure's source is no
longer the same.

**Rendering reads nothing else from the workspace.** Page presets are copied
into the figure when it is created. `preferences.json` only seeds new figures,
the journal catalog is part of the app, and thumbnails are not used for export.
Fonts come from the system (`page.preset.font_family`): a different machine can
lay text out differently.

**The provenance** reported with the tree contains:

- the project's id and name;
- the figure's id and name;
- the export log entry: time, format, files with their SHA-256, the sidecar's
  SHA-256, the entry's hash and the previous hash;
- where the raw images came from (`raw_members_from`);
- the status of the project's hash-chained log (`ok`, `broken`, …);
- the Pycrograph version.

## Several windows or installations

Two Pycrograph processes can open the same workspace: two windows, or a
standalone Pycrograph and the copy bundled in Pyblication. Each project
therefore has an **edit lock**, `projects/<id>/.lock`, so that only one of them
edits it at a time.

- Opening a project creates the lock atomically. The lock records the host,
  process id, app and version, start time (UTC), Python executable and a random
  token. It is removed when the project is closed or the app quits, and only if
  it still carries this window's token.
- If another live Pycrograph holds the lock, you choose:
  - **Open read-only** (the default);
  - **Open anyway**, which takes the lock over;
  - **Cancel**, which keeps the current project open.

  The dialog names the holder and when it opened the project, in local time.
- A lock left by a crash is **stale**: same computer, process no longer running.
  It is taken over without asking. A lock written on another computer, or one
  that cannot be read, is never taken over silently.
- **Read-only** shows a banner and "— read-only" in the title. Nothing is saved,
  so no log entry reaches disk. Images can't be imported, copied or relinked, and
  nothing can be exported (figure, source data, archive, integrity report),
  because exports are recorded in the log. The first change you make says once
  that it is not saved. To edit, reopen the project after the other window has
  closed it.
- Before every save, a window checks that the lock still carries its token. If
  another window chose "Open anyway", the first one turns read-only and says
  that its last change was not saved.
- Releasing a lock that leaves `projects/<id>/` empty removes the folder, but only
  when it is truly empty. Any other file or folder keeps it.

Reading never needs the lock: the Pyblication plugin and `.pcarchive` export
ignore it, and it is never put into an archive.

## Newer projects

A project saved by a newer Pycrograph (a higher project format) is listed as
"*name* (needs a newer Pycrograph)", and opening it is refused. This holds for
every way of loading a project, archive import included. A save never overwrites
a `project.json` that a newer Pycrograph wrote in the meantime, for example
after "Open anyway". It also never overwrites one it cannot read: the save is
refused with a message, and the file is left as it is. Saves are atomic: a temporary file in the same folder,
flushed, then renamed over `project.json`.
