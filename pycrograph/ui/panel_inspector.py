# Pycrograph
# SPDX-License-Identifier: GPL-3.0-only
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.

"""
Preview inspector (figure editor §2.2 / §2.3): edits ALL selected panels.

- A control whose value differs between the selected panels shows a mixed
  state (blank field, partially checked box, hidden slider handle);
  touching it sets every selected panel.
- Channel controls (ui/channel_controls.py, reused) are matched by channel
  index; panels whose image lacks that channel are skipped and the status
  bar says how many. The levels range is the largest full scale among the
  selected images; values are clamped per image.
- The first selected panel (figure order) is the reference: its crop feeds
  the histograms, saturation badges and the viewport mini-view.

The inspector never writes the model itself: every change goes through
FiguresTab.apply(), i.e. FigureEditor.edit() (one undo step and one log
entry per panel per gesture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout,
    QWidget,
)

from .. import figures as F
from .. import layout as L
from .. import viewport as V
from ..display import auto_window, compute_saturation_stats
from ..models import ChannelInfo, ChannelSettings, ImageRef, Panel
from ..render import RenderError
from .channel_controls import HIDDEN_HANDLE, MIXED_TEXT, ChannelControls

if TYPE_CHECKING:                                   # pragma: no cover
    from .figures_tab import FiguresTab

MINI_SIDE = 240
SECTION_STYLE = """
    QFrame#section { background: #f4f4f4; border: 1px solid #d2d2d2; border-radius: 8px; }
"""


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("section")
    frame.setStyleSheet(SECTION_STYLE)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 8, 10, 10)
    lbl = QLabel(title)
    lbl.setStyleSheet("font-weight: 600; font-size: 12px;")
    lay.addWidget(lbl)
    return frame, lay


def _values(panels: list[Panel], get: Callable[[Panel], Any]) -> list:
    out = []
    for p in panels:
        v = get(p)
        if v is not None and v not in out:
            out.append(v)
    return out



def lock_text(panels: list[Panel], fig) -> str:
    """The lock indicator, by kind and size — never the internal group id.
    '🔒 Locked with N other panel(s) (split row)'; '🔒 Mixed: montage, manual
    lock + unlocked panels'."""
    groups = [p.lock_group for p in panels]
    named = list(dict.fromkeys(g for g in groups if g))
    if not named:
        return "🔓 Not locked"
    if len(named) == 1 and all(groups):
        g = named[0]
        members = sum(1 for q in fig.panels if q.lock_group == g) if fig else len(panels)
        others = members - len(panels)
        if others > 0:
            return f"🔒 Locked with {others} other panel(s) ({L.lock_kind(g)})"
        return f"🔒 Locked: the whole group is selected ({members} panels, {L.lock_kind(g)})"
    kinds = list(dict.fromkeys(L.lock_kind(g) for g in named))
    return "🔒 Mixed: " + ", ".join(kinds) + (" + unlocked panels" if not all(groups) else "")

class MiniView(QGraphicsView):
    """Whole image (oriented like the panel) + the crop rectangle.
    Drag = pan, wheel = zoom."""

    def __init__(self, inspector: "PreviewInspector"):
        super().__init__()
        self._insp = inspector
        self.setFixedHeight(MINI_SIDE)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pix = QGraphicsPixmapItem()
        self.scene().addItem(self.pix)
        self.rect_item = QGraphicsRectItem()
        pen = QPen(QColor("#facc15"), 2)
        pen.setCosmetic(True)
        self.rect_item.setPen(pen)
        self.scene().addItem(self.rect_item)
        self._k = 1.0                   # mini-view px per oriented image px
        self._last: Optional[QPointF] = None

    def set_image(self, pixmap: Optional[QPixmap], k: float) -> None:
        self._k = k
        self.pix.setPixmap(pixmap or QPixmap())
        r = QRectF(self.pix.boundingRect())
        self.scene().setSceneRect(r.adjusted(-r.width(), -r.height(), r.width(), r.height()))

    def set_crop_rect(self, rect: Optional[tuple[float, float, float, float]]) -> None:
        if rect is None:
            self.rect_item.setVisible(False)
            return
        x, y, w, h = (v * self._k for v in rect)
        self.rect_item.setRect(x, y, w, h)
        self.rect_item.setVisible(True)
        box = self.pix.boundingRect().united(self.rect_item.rect())
        self.fitInView(box.adjusted(-4, -4, 4, 4), Qt.KeepAspectRatio)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._last = self.mapToScene(event.position().toPoint())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._last is None:
            return
        p = self.mapToScene(event.position().toPoint())
        d = p - self._last
        self._last = p
        if self._k > 0 and (d.x() or d.y()):
            self._insp.pan_by_oriented(d.x() / self._k, d.y() / self._k, final=False)

    def mouseReleaseEvent(self, event) -> None:
        if self._last is not None:
            self._last = None
            self._insp.end_live()

    def wheelEvent(self, event) -> None:
        self._insp.zoom_by(1.1 if event.angleDelta().y() > 0 else 1 / 1.1, final=False)
        event.accept()


class PreviewInspector(QScrollArea):
    def __init__(self, tab: "FiguresTab"):
        super().__init__()
        self.tab = tab
        self.setWidgetResizable(True)
        self.panels: list[Panel] = []
        self.controls: dict[int, ChannelControls] = {}
        self._body = QWidget()
        self._lay = QVBoxLayout(self._body)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._lay.setSpacing(8)
        self.setWidget(self._body)
        self.set_panels([])

    # ================================================================ build
    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # deleteLater only runs back in the event loop: hide and
                # detach now so a rebuild never shows the old widgets.
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.controls.clear()

    def set_panels(self, panels: list[Panel]) -> None:
        """Rebuild for a new selection (or after undo / a one-shot edit)."""
        self._clear()
        self.panels = [p for p in panels if self.tab.ref(p) is not None]
        if not self.panels:
            lbl = QLabel("Select one or more panels." if not panels else "Image missing from project.")
            lbl.setStyleSheet("color: #6b7280;")
            self._lay.addWidget(lbl)
            self._lay.addStretch(1)
            return
        ref0 = self.tab.ref(self.panels[0])
        title = QLabel(ref0.filename if len(self.panels) == 1 else
                       f"{len(self.panels)} panels — first: {ref0.filename}")
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        title.setWordWrap(True)
        self._lay.addWidget(title)
        self._build_lock()
        self._build_viewport()
        self._build_tz()
        self._build_channels()
        self._build_actions()
        self._lay.addStretch(1)
        self.refresh_values()
        self.refresh_planes()

    def _build_lock(self) -> None:
        """Lock indicator + toggle. A lock shares LUT, black, white, gamma and
        invert per channel index between the panels of a group."""
        frame, lay = _section("Lock (shared levels / LUT)")
        groups = [p.lock_group for p in self.panels]
        fig = self.tab.current_figure()
        named = sorted({g for g in groups if g})
        self.lock_label = QLabel(lock_text(self.panels, fig))
        self.lock_label.setWordWrap(True)
        self.lock_label.setToolTip("Editing a channel's LUT, levels, gamma or invert on a locked "
                                   "panel applies it to the same channel of every panel of the "
                                   "group. Visibility, T/Z, crop, orientation and labels stay "
                                   "per panel.")
        lay.addWidget(self.lock_label)
        row = QHBoxLayout()
        self.lock_buttons = {}
        for key, text, slot, enabled in (
                ("lock", "Lock selection", self.tab.lock_selection, len(self.panels) >= 2),
                ("unlock", "Unlock", self.tab.unlock_selection, bool(named)),
                ("select", "Select group", self.tab.select_lock_group, bool(named))):
            b = QPushButton(text)
            b.setEnabled(enabled)
            b.clicked.connect(lambda _c=False, s_=slot: s_())
            self.lock_buttons[key] = b
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._lay.addWidget(frame)

    def _build_viewport(self) -> None:
        frame, lay = _section("Viewport (crop)")
        self.mini = MiniView(self)
        lay.addWidget(self.mini)
        row = QHBoxLayout()
        row.addWidget(QLabel("Zoom"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(int(V.MIN_ZOOM_PCT), int(V.MAX_ZOOM_PCT))
        self.zoom_spin.setSuffix(" %")
        self.zoom_spin.setKeyboardTracking(False)
        self.zoom_spin.setToolTip("100 % = whole image fitted in the panel")
        self.zoom_spin.valueChanged.connect(self._on_zoom_typed)
        row.addWidget(self.zoom_spin)
        for text, tip, slot in (("−", "Zoom out", lambda: self.zoom_by(1 / 1.25)),
                                ("+", "Zoom in", lambda: self.zoom_by(1.25)),
                                ("Fit", "Whole image in the panel", self.fit_viewport)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(40 if len(text) == 1 else 48)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.orient_buttons = {}
        for key, text, tip, slot in (
                ("ccw", "⟲", "Rotate 90° counter-clockwise", lambda: self.rotate(-90)),
                ("cw", "⟳", "Rotate 90° clockwise", lambda: self.rotate(90)),
                ("fh", "⇋", "Flip horizontally", lambda: self.flip("flip_horizontal")),
                ("fv", "⇵", "Flip vertically", lambda: self.flip("flip_vertical"))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(36)
            b.clicked.connect(slot)
            self.orient_buttons[key] = b
            row.addWidget(b)
        self.orient_label = QLabel()
        row.addWidget(self.orient_label, 1)
        lay.addLayout(row)
        self._lay.addWidget(frame)

    def _build_tz(self) -> None:
        frame, lay = _section("Time and Z")
        grid = QGridLayout()
        self.t_label, self.z_label = QLabel("T"), QLabel("Z")
        self.t_slider, self.z_slider = QSlider(Qt.Horizontal), QSlider(Qt.Horizontal)
        refs = [self.tab.ref(p) for p in self.panels]
        self._max_t = max(r.metadata.shape.T for r in refs)
        self._max_z = max(r.metadata.shape.Z for r in refs)
        for row, (lbl, sl, n, axis) in enumerate(((self.t_label, self.t_slider, self._max_t, "t"),
                                                   (self.z_label, self.z_slider, self._max_z, "z"))):
            sl.setRange(0, max(0, n - 1))
            sl.setEnabled(n > 1)
            sl.valueChanged.connect(lambda v, a=axis: self._on_tz(a, v))
            sl.sliderReleased.connect(self.end_live)
            sl.sliderPressed.connect(lambda s=sl: s.setStyleSheet(""))
            lbl.setMinimumWidth(90)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(sl, row, 1)
        lay.addLayout(grid)
        prow = QHBoxLayout()
        self.proj_cb = QCheckBox("Max projection, Z")
        self.proj_cb.setToolTip("Maximum-intensity projection over a Z range (display only)")
        self.proj_cb.clicked.connect(self._on_proj_toggled)
        self.proj_from, self.proj_to = QSpinBox(), QSpinBox()
        for sp in (self.proj_from, self.proj_to):
            sp.setRange(1, max(1, self._max_z))
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._on_proj_range)
        prow.addWidget(self.proj_cb)
        prow.addWidget(self.proj_from)
        prow.addWidget(QLabel("to"))
        prow.addWidget(self.proj_to)
        prow.addStretch(1)
        self.proj_cb.setEnabled(self._max_z > 1)
        lay.addLayout(prow)
        self._lay.addWidget(frame)

    def _build_channels(self) -> None:
        refs = {p.id: self.tab.ref(p) for p in self.panels}
        indices = sorted({c for r in refs.values() for c in range(r.metadata.shape.C)})
        for idx in indices:
            having = [p for p in self.panels if idx < refs[p.id].metadata.shape.C]
            first_ref = refs[having[0].id]
            info = next((c for c in first_ref.metadata.channels if c.index == idx),
                        ChannelInfo(index=idx))
            fs = max(refs[p.id].metadata.full_scale for p in having)
            cs = F.panel_channel(having[0], idx) or ChannelSettings(index=idx, white=fs)
            ctrl = ChannelControls(info, cs, fs)
            ctrl.stain_edit.hide()                   # stains are library annotations
            if len(having) < len(self.panels):
                ctrl.name_lbl.setToolTip(ctrl.name_lbl.toolTip() +
                                         f"\nPresent in {len(having)} of {len(self.panels)} panels")
            ctrl.changed.connect(self._on_channel_changed)
            ctrl.auto_requested.connect(self._on_auto)
            self.controls[idx] = ctrl
            self._lay.addWidget(ctrl)

    def _build_actions(self) -> None:
        frame, lay = _section("Copy / paste and defaults")
        grid = QGridLayout()
        t = self.tab
        buttons = (
            ("copy_rendering", "Copy rendering", t.copy_rendering, 0, 0),
            ("paste_rendering", "Paste rendering", t.paste_rendering, 0, 1),
            ("copy_viewport", "Copy viewport", t.copy_viewport, 1, 0),
            ("paste_viewport", "Paste viewport", t.paste_viewport, 1, 1),
            ("reset_defaults", "Reset to image defaults", t.reset_to_defaults, 2, 0),
            ("save_defaults", "Save as image defaults", t.save_as_defaults, 2, 1),
            ("replace", "Replace image…", t.replace_image_dialog, 3, 0),
        )
        self.buttons: dict[str, QPushButton] = {}
        for key, text, slot, r, c in buttons:
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, s=slot: s())
            self.buttons[key] = b
            grid.addWidget(b, r, c)
        self.buttons["paste_rendering"].setEnabled(t.clip_rendering is not None)
        self.buttons["paste_viewport"].setEnabled(t.clip_viewport is not None)
        single = len(self.panels) == 1
        self.buttons["copy_rendering"].setEnabled(single)
        self.buttons["copy_viewport"].setEnabled(single)
        self.buttons["save_defaults"].setEnabled(single)
        self.buttons["save_defaults"].setToolTip(
            "Copy this panel's rendering, T/Z and orientation to its library image"
            if single else "Select a single panel")
        lay.addLayout(grid)
        self._lay.addWidget(frame)

    # ============================================================ display
    def refresh_values(self, channels: bool = True) -> None:
        """Re-read the model into the controls (no rebuild). During a live
        gesture (channels=False) the channel controls being dragged are
        left alone."""
        if not self.panels:
            return
        P = self.panels
        refs = [self.tab.ref(p) for p in P]
        # zoom
        zooms = _values(P, lambda p: round(V.zoom_percent(p, self.tab.ref(p).metadata.shape.X,
                                                          self.tab.ref(p).metadata.shape.Y)))
        self.zoom_spin.blockSignals(True)
        if len(zooms) == 1:
            self.zoom_spin.setSpecialValueText("")
            self.zoom_spin.setMinimum(int(V.MIN_ZOOM_PCT))
            self.zoom_spin.setValue(int(zooms[0]))
        else:
            self.zoom_spin.setMinimum(int(V.MIN_ZOOM_PCT) - 1)
            self.zoom_spin.setSpecialValueText(MIXED_TEXT)
            self.zoom_spin.setValue(int(V.MIN_ZOOM_PCT) - 1)
        self.zoom_spin.blockSignals(False)
        rots = _values(P, lambda p: p.rotation_deg)
        fhs = _values(P, lambda p: p.flip_horizontal)
        fvs = _values(P, lambda p: p.flip_vertical)

        def txt(vals, fmt):
            return fmt(vals[0]) if len(vals) == 1 else "mixed"
        self.orient_label.setText(f"Rotation {txt(rots, lambda v: f'{v}°')} · "
                                  f"flip H {txt(fhs, lambda v: 'on' if v else 'off')} · "
                                  f"V {txt(fvs, lambda v: 'on' if v else 'off')}")
        # T / Z
        for axis, sl, lbl, n in (("t", self.t_slider, self.t_label, self._max_t),
                                 ("z", self.z_slider, self.z_label, self._max_z)):
            vals = _values(P, lambda p, a=axis: getattr(p, a))
            sl.blockSignals(True)
            if len(vals) == 1:
                sl.setValue(vals[0])
                if not sl.isSliderDown():
                    sl.setStyleSheet("")
                lbl.setText(f"{axis.upper()} {vals[0] + 1}/{n}")
            else:
                sl.setStyleSheet(HIDDEN_HANDLE)
                lbl.setText(f"{axis.upper()} mixed")
            sl.blockSignals(False)
        projs = _values(P, lambda p: (p.z_projection.z_start, p.z_projection.z_end)
                        if p.z_projection else "none")
        self.proj_cb.blockSignals(True)
        self.proj_cb.setTristate(False)
        if len(projs) > 1 and "none" in projs:
            self.proj_cb.setCheckState(Qt.PartiallyChecked)
        else:
            self.proj_cb.setChecked(projs[0] != "none")
        self.proj_cb.blockSignals(False)
        ranges = [v for v in projs if v != "none"]
        for sp, i in ((self.proj_from, 0), (self.proj_to, 1)):
            sp.blockSignals(True)
            vals = sorted({r[i] for r in ranges})
            if len(vals) == 1:
                sp.setSpecialValueText("")
                sp.setMinimum(1)
                sp.setValue(vals[0] + 1)
            elif vals:
                sp.setMinimum(0)
                sp.setSpecialValueText(MIXED_TEXT)
                sp.setValue(0)
            else:
                sp.setSpecialValueText("")
                sp.setMinimum(1)
                sp.setValue(1 if i == 0 else max(1, self._max_z))
            sp.setEnabled(bool(ranges))
            sp.blockSignals(False)
        self._update_mini_rect()
        if not channels:
            return
        for idx, ctrl in self.controls.items():
            having = [p for p, r in zip(P, refs) if idx < r.metadata.shape.C]
            settings = [F.panel_channel(p, idx) for p in having]
            settings = [s for s in settings if s is not None]
            if not settings:
                continue
            mixed = {f for f in F.CHANNEL_FIELDS
                     if len({getattr(s, f) for s in settings}) > 1}
            ctrl.set_settings(settings[0])
            if mixed:
                ctrl.set_mixed(mixed)

    def refresh_planes(self) -> None:
        """Mini-view image, histograms and saturation of the reference
        panel's crop (after T/Z/viewport gestures end)."""
        if not self.panels:
            return
        p0 = self.panels[0]
        ref = self.tab.ref(p0)
        s = ref.metadata.shape
        full = p0.model_copy(update={"crop": None})
        try:
            rgb = self.tab.renderer.render(ref, full, max_side=MINI_SIDE)
        except RenderError:
            self.mini.set_image(None, 1.0)
            for ctrl in self.controls.values():
                ctrl.hist.clear()
                ctrl.set_saturation(None)
            return
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        ow, _oh = V.oriented_wh(s.X, s.Y, p0.rotation_deg)
        self.mini.set_image(QPixmap.fromImage(img), w / ow)
        self._update_mini_rect()
        for idx, ctrl in self.controls.items():
            if idx >= s.C:
                ctrl.hist.clear()
                ctrl.set_saturation(None)
                continue
            plane = self.tab.renderer.crop_plane(ref, p0, idx)
            ctrl.set_plane(plane, compute_saturation_stats(plane, ref.metadata.full_scale))

    def _update_mini_rect(self) -> None:
        if not self.panels:
            return
        p0 = self.panels[0]
        s = self.tab.ref(p0).metadata.shape
        c = V.effective_crop(p0, s.X, s.Y)
        self.mini.set_crop_rect(V.orient_rect((c.x, c.y, c.w, c.h), (0, 0, s.X, s.Y),
                                              p0.rotation_deg, p0.flip_horizontal,
                                              p0.flip_vertical))

    # ============================================================ actions
    def _apply(self, fn, label: str, key: Optional[str] = None, final: bool = True,
               what: str = "") -> None:
        self.tab.apply(fn, label, key=key, final=final, what=what)

    def end_live(self) -> None:
        self.tab.end_live()

    def zoom_by(self, factor: float, final: bool = True) -> None:
        self._apply(lambda p, r: V.zoom_by(p, r.metadata.shape.X, r.metadata.shape.Y, factor),
                    "Zoom", key="viewport.zoom", final=final)

    def _on_zoom_typed(self, pct: int) -> None:
        if pct < V.MIN_ZOOM_PCT:
            return
        self._apply(lambda p, r: V.set_zoom_percent(p, r.metadata.shape.X, r.metadata.shape.Y, pct),
                    "Zoom")

    def fit_viewport(self) -> None:
        self._apply(lambda p, r: V.fit(p, r.metadata.shape.X, r.metadata.shape.Y), "Fit viewport")

    def pan_by_oriented(self, du: float, dv: float, final: bool = True) -> None:
        """An on-screen displacement (du right, dv down, in oriented image
        pixels) applied to every selected panel through ITS OWN orientation:
        each image moves the same way on the page, whatever its rotation
        or flips."""
        def pan(p: Panel, r: ImageRef) -> bool:
            dx, dy = V.unorient_vector(du, dv, p.rotation_deg, p.flip_horizontal, p.flip_vertical)
            V.pan(p, r.metadata.shape.X, r.metadata.shape.Y, dx, dy)
            return True
        self._apply(pan, "Pan", key="viewport.pan", final=final)

    def rotate(self, delta: int) -> None:
        self._apply(lambda p, r: F.rotate(p, r, delta), "Rotate")
        self.refresh_planes()

    def flip(self, attr: str) -> None:
        self._apply(lambda p, r: F.flip(p, r, attr), "Flip")
        self.refresh_planes()

    def _on_tz(self, axis: str, value: int) -> None:
        sl = self.t_slider if axis == "t" else self.z_slider
        fn = F.set_t if axis == "t" else F.set_z
        self._apply(lambda p, r: fn(p, r, value), axis.upper(), key=f"tz.{axis}",
                    final=not sl.isSliderDown())
        if not sl.isSliderDown():
            self.refresh_planes()

    def _on_proj_toggled(self, checked: bool) -> None:
        self.proj_cb.setTristate(False)
        if checked:
            a, b = self.proj_from.value() - 1, self.proj_to.value() - 1
            if a < 0 or b < 0 or a == b:
                a, b = 0, self._max_z - 1
            self._apply(lambda p, r: F.set_projection(p, r, (a, b)), "Max projection",
                        what="single-Z image")
        else:
            self._apply(lambda p, r: F.set_projection(p, r, None), "No projection")
        self.refresh_planes()

    def _on_proj_range(self, _v: int) -> None:
        a, b = self.proj_from.value() - 1, self.proj_to.value() - 1
        if a < 0 or b < 0:
            return                                     # still showing "mixed"
        self._apply(lambda p, r: F.set_projection(p, r, (a, b)) if p.z_projection else False,
                    "Projection range")
        self.refresh_planes()

    def _on_channel_changed(self, index: int, field: str, value, final: bool) -> None:
        # The typed value goes to locked panels too, each clamping it to its
        # own bit depth (not the first panel's clamped copy).
        self.tab.apply(lambda p, r: F.set_channel_field(p, r, index, field, value),
                       f"Channel {index + 1} {field}", key=f"channels[{index}].{field}",
                       final=final, what=f"no channel {index + 1}",
                       lock_values={(index, field): value} if field in F.LOCKED_FIELDS else None)

    def _on_auto(self, index: int) -> None:
        def auto(p: Panel, r: ImageRef) -> bool:
            if index >= r.metadata.shape.C:
                return False
            lo, hi = auto_window(self.tab.renderer.crop_plane(r, p, index))
            return F.set_levels(p, r, index, lo, min(hi, r.metadata.full_scale))
        self._apply(auto, f"Channel {index + 1} auto levels", what=f"no channel {index + 1}")
