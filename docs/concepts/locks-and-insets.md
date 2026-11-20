# Locks and insets

## Locks

How to: {doc}`../guide/locks` (step by step, with worked cases).

Panels in one **lock group** share, per channel index, **black, white, gamma,
LUT and invert** — never visibility, T / Z, projection, crop, orientation or
labels. Editing channel *i* on a locked panel applies it to channel *i* of every
panel of the group, clamped to each image's bit depth; panels without that
channel are skipped. It is one undo step and one log entry per panel changed.

*Lock selection* (Preview tab) makes the others take the first panel's settings
(after asking when they differ); *Unlock* and *Select group* complete it. The
auto-layouts lock their panels: a split-channel row keeps each panel's own
channel visibility while sharing levels. A warning appears when panels sharing
settings have a different channel name or wavelength at the same index.

## Insets

How to: {doc}`../guide/insets` (step by step, with worked cases).

*Create inset* (Insets & Shapes tab): draw a rectangle on a panel; a new panel
shows that region at twice the magnification, placed to the right. The
rectangle drawn on the parent is **derived** from the inset's crop: pan, zoom or
resize the inset and the rectangle follows; drag or resize the rectangle on the
selected parent and the inset follows. Insets are numbered and coloured to
match (rectangle, number and border on the inset). With *Follow the parent's
T / Z* and *Follow the parent's channels*, the inset updates with every edit of
the parent. Deleting a parent turns its insets into ordinary panels; copying a
parent with its insets keeps the link in the pasted copies.

## Replicated insets

*Replicate to…* puts the same inset on the panels ticked in its checklist —
the channels of a split row, the conditions of a grid — at the same physical
magnification (µm per page-mm), colour, width and follow flags. Each copy goes
directly below its own parent (same gap and left alignment as the source)
when the source sits below its parent or the parents form a row. Otherwise it
goes at the source's offset from its parent, or the nearest free place.
For each target Pycrograph checks the geometry against the source image:

| Compatibility | Meaning | Possible link |
|---|---|---|
| same image | the same file (e.g. split channels) | same region or same size |
| same geometry | same width × height and pixel size (within 10⁻⁶ µm) | same region or same size |
| different | another size or pixel size | same size only |

- **Same region** shows the *same raw-pixel rectangle* in every image. Across
  separate files this assumes the images are **registered** (the same field):
  the dialog shows whether the MetaMorph stage positions agree, differ or are
  unknown, and warns unless they agree.
- **Same size** shows a region of the *same physical size* (µm) in every image,
  centred at the same relative position and kept inside the image; each inset
  can then be moved on its own.

The dialog selects *Same region* only when every ticked target is the same
image, or the same geometry at the same stage position; otherwise *Same
size*. It follows the checks until the user picks a mode.

With **Keep linked** the insets form a group, and a group is **one inset
repeated**: all members have the same page size and orientation (rotation,
flips) and show the same region — the same rectangle of pixels for "same
region" members, exactly the same µm size around each one's own centre (at the
same magnification) for "same size" members. Only each inset's position on the
page is its own. Whatever changes one member — panning or zooming in the
Preview tab, dragging its rectangle on the parent, resizing it on the canvas,
*Same width / height*, *Align magnification*, rotating or flipping it,
pasting a viewport — changes the others in the same step; the status bar says
"applies to the N linked insets". If several members change in one step, the
first one acted on wins. A raw rectangle is never copied between images of
different geometry. The whole change is one undo step with one log entry per
inset; the consistency check warns if a group ever diverges. *Unlink from
group*, *Make ordinary panel* or deleting an inset take it out of the group (a
group left with one inset disappears). Copying a whole group gives the copies a
new group of their own; copying only part of it leaves the copies unlinked.
