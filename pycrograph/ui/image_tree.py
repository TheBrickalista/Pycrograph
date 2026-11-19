# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
The project's image tree (Project › Dataset › Image), shared by the Library
tab (LibraryPanel) and the Add Images picker of the Figures tab, so the two
never drift: same structure, labels, status suffixes, tooltips, thumbnails
and filtering; also the metadata block shown for an image.

Item data (column 0, ROLE): ("project", id, ""), ("dataset", id, ""),
("image", image id, dataset id).
"""

from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..models import ImageRef, Project

ROLE = Qt.UserRole
STATUS_TEXT = {
    "ok": "",
    "pending_hash": "  ⏳ hashing",
    "missing": "  ⚠ missing",
    "changed": "  ⏳ verifying",
    "modified": "  ⚠ MODIFIED",
}
STATUS_REASON = {
    "pending_hash": "SHA-256 being computed",
    "missing": "source file not found (drive unplugged, moved or renamed?) — Relink it in the "
               "Library",
    "changed": "file size or date changed since import; being re-hashed",
    "modified": "content differs from the imported file (SHA-256 mismatch)",
}


def populate_image_tree(tree: QTreeWidget, project: Optional[Project], visible_ids: set[str],
                        thumbs: dict[str, QIcon], status: dict[str, str], filtering: bool = False,
                        disable_missing: bool = False) -> None:
    """(Re)fill `tree`: the project root, its non-hidden datasets (with their
    image count), and the images in `visible_ids`. With `filtering`, a
    dataset left empty is hidden. With `disable_missing`, images whose
    status is "missing" are greyed and not selectable (reason in the
    tooltip). Signals are blocked while filling."""
    tree.blockSignals(True)
    tree.clear()
    if project is not None:
        root = QTreeWidgetItem([project.project.name])
        root.setData(0, ROLE, ("project", project.project.id, ""))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        tree.addTopLevelItem(root)
        for ds in project.datasets:
            if ds.hidden:
                continue
            ds_item = QTreeWidgetItem([f"{ds.name}  ({len(ds.image_ids)})"])
            ds_item.setData(0, ROLE, ("dataset", ds.id, ""))
            root.addChild(ds_item)
            for iid in ds.image_ids:
                ref = project.images.get(iid)
                if ref is None or iid not in visible_ids:
                    continue
                st = status.get(iid, "")
                it = QTreeWidgetItem([ref.filename + STATUS_TEXT.get(st, "")])
                it.setData(0, ROLE, ("image", iid, ds.id))
                tip = ref.path
                if st in STATUS_REASON:
                    tip += f"\n{STATUS_REASON[st]}"
                it.setToolTip(0, tip)
                if iid in thumbs:
                    it.setIcon(0, thumbs[iid])
                if disable_missing and st == "missing":
                    it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
                    it.setForeground(0, QBrush(QColor("#9ca3af")))
                ds_item.addChild(it)
            ds_item.setExpanded(True)
            if filtering and ds_item.childCount() == 0:
                ds_item.setHidden(True)
        root.setExpanded(True)
    tree.blockSignals(False)


def item_data(item: Optional[QTreeWidgetItem]):
    return item.data(0, ROLE) if item is not None else None


def iter_image_items(tree: QTreeWidget):
    """Image items in tree order (hidden datasets' children excluded)."""
    root = tree.topLevelItem(0)
    if root is None:
        return
    for i in range(root.childCount()):
        ds = root.child(i)
        if ds.isHidden():
            continue
        for j in range(ds.childCount()):
            yield ds.child(j)


def metadata_html(ref: Optional[ImageRef], status: str = "ok") -> str:
    """The metadata block of an image (Library info pane, Add Images preview)."""
    if ref is None:
        return "<i>Select an image.</i>"
    m = ref.metadata
    cal = m.calibration
    rows = [
        ("File", html.escape(ref.filename)),
        ("Path", f"<small>{html.escape(ref.path)}</small>"),
        ("Status", html.escape(status or "ok")),
        ("SHA-256", f"<small>{ref.sha256 or 'computing…'}</small>"),
        ("Size", f"{ref.size_bytes / 2**20:.1f} MB"),
        ("Reader", html.escape(f"{ref.reader} ({ref.reader_version})")),
        ("Dimensions", f"T {m.shape.T} · Z {m.shape.Z} · C {m.shape.C} · {m.shape.X}×{m.shape.Y} px"),
        ("Pixel type", f"{m.dtype} ({m.bit_depth}-bit)"),
        ("Pixel size", (f"{cal.pixel_size_x:g} × {cal.pixel_size_y:g} {cal.unit}/px"
                        f" <small>({cal.source})</small>") if cal.is_calibrated else
         "<span style='color:#b45309'>not calibrated</span>"),
    ]
    if cal.z_step:
        rows.append(("Z step", f"{cal.z_step:g} {cal.unit}"))
    if m.objective or m.numerical_aperture:
        rows.append(("Objective", html.escape(f"{m.objective or ''} NA {m.numerical_aperture or '?'}")))
    if m.acquisition_time:
        rows.append(("Acquired", html.escape(m.acquisition_time)))
    if m.time_offsets_s and len(m.time_offsets_s) > 1:
        rows.append(("Time span", f"{m.time_offsets_s[-1]:g} s"))
    for ch in m.channels:
        bits = [ch.name or f"Channel {ch.index + 1}"]
        if ch.wavelength_nm:
            bits.append(f"{ch.wavelength_nm:g} nm")
        if ch.exposure_ms:
            bits.append(f"{ch.exposure_ms:g} ms")
        rows.append((f"Ch {ch.index + 1}", html.escape(" · ".join(bits))))
    for w in ref.warnings:
        rows.append(("⚠", f"<span style='color:#b45309'>{html.escape(w)}</span>"))
    body = "".join(f"<tr><td style='color:#6b7280;padding-right:8px'>{k}</td><td>{v}</td></tr>"
                   for k, v in rows)
    return f"<table style='font-size:11px'>{body}</table>"
