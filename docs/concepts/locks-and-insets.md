# Locks and insets

## Locks

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

*Create inset* (Insets & Shapes tab): draw a rectangle on a panel; a new panel
shows that region at twice the magnification, placed to the right. The
rectangle drawn on the parent is **derived** from the inset's crop: pan, zoom or
resize the inset and the rectangle follows; drag or resize the rectangle on the
selected parent and the inset follows. Insets are numbered and coloured to
match (rectangle, number and border on the inset). With *Follow the parent's
T / Z* and *Follow the parent's channels*, the inset updates with every edit of
the parent. Deleting a parent turns its insets into ordinary panels; copying a
parent with its insets keeps the link in the pasted copies.
