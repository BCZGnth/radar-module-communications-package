"""
LD2450 Radar Grapher
────────────────────
Each frame is a 12-element array:
  [x1, y1, speed1, dist_res1,  x2, y2, speed2, dist_res2,  x3, y3, speed3, dist_res3]
  coordinates in mm, speed in cm/s, dist_res in mm.

Features
• 2-D scatter plot of up to 3 targets
• Velocity vectors derived from consecutive position deltas (toggleable)
• Trail showing last N positions per target
• Playback controls: Play/Pause, Prev/Next, speed selector
• Scrub slider
• Open JSON File button — accepts array-of-arrays or single array
• Status bar with frame info and per-target live readout
"""

import sys
import json
import math
from pathlib import Path
from collections import deque

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSlider, QFileDialog, QMessageBox,
    QComboBox, QStatusBar, QCheckBox,
)
from PyQt6.QtCore import QTimer, Qt
import pyqtgraph as pg
from pyqtgraph import ArrowItem
import numpy as np

# ── Theme ─────────────────────────────────────────────────────────────────────
pg.setConfigOption("background", "#0a0e1a")
pg.setConfigOption("foreground", "#c8d6e5")

TARGET_COLORS = ["#00f5d4", "#f5a623", "#e84393"]   # teal / amber / pink per target
TRAIL_ALPHA   = 80          # 0-255
TRAIL_LEN     = 20          # frames of trail
PANEL_BG      = "#111827"
BORDER_CLR    = "#1e2d3d"
VECTOR_SCALE  = 8.0         # mm per mm/frame  (tune visually)

BTN_STYLE = (
    "QPushButton {"
    f"  background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
    "  border-radius:4px; padding:4px 10px; font-family:'Courier New'; font-size:12px;"
    "}"
    "QPushButton:hover   { background:#1e2d3d; }"
    "QPushButton:pressed { background:#0a0e1a; }"
    "QPushButton:disabled { color:#3a4a5a; }"
)
SLIDER_STYLE = (
    "QSlider::groove:horizontal { height:4px; background:#1e2d3d; border-radius:2px; }"
    "QSlider::handle:horizontal { background:#00f5d4; width:14px; height:14px;"
    "  margin:-5px 0; border-radius:7px; }"
    "QSlider::sub-page:horizontal { background:#00f5d4; border-radius:2px; }"
)
SPEEDS = {"0.25×": 400, "0.5×": 200, "1×": 100, "2×": 50, "4×": 25}


# ── Data helpers ──────────────────────────────────────────────────────────────
def _parse_frame(raw, idx: int) -> tuple[list[dict] | None, list[str]]:
    """
    Parse one raw frame (12-element list) into a list of 3 target dicts.
    Returns (targets, warnings).  targets is None on unrecoverable error.
    """
    warns = []
    if not isinstance(raw, (list, tuple)):
        return None, [f"Frame {idx}: not a list — skipped."]
    if len(raw) < 12:
        warns.append(f"Frame {idx}: expected 12 values, got {len(raw)} — padding with 0.")
        raw = list(raw) + [0] * (12 - len(raw))

    targets = []
    for t in range(3):
        base = t * 4
        try:
            x        = float(raw[base])
            y        = float(raw[base + 1])
            speed    = float(raw[base + 2])
            dist_res = float(raw[base + 3])
        except (TypeError, ValueError) as e:
            warns.append(f"Frame {idx} target {t+1}: bad value ({e}) — defaulted to 0.")
            x = y = speed = dist_res = 0.0
        targets.append({"x": x, "y": y, "speed": speed, "dist_res": dist_res})
    return targets, warns


def _load_json_file(path: str) -> tuple[list[list[dict]], list[str]]:
    """
    Load and parse a LD2450 JSON file.
    Returns (frames, warnings) where each frame is a list of 3 target dicts.
    Raises FileNotFoundError / ValueError on hard failures.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found:\n{path}")
    if p.stat().st_size == 0:
        raise ValueError("The selected file is empty.")

    with open(p, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON.\n\nDetail: {e}") from e

    # Accept a single frame (list of 12 numbers) or array of frames
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], (int, float)):
        data = [data]   # single bare frame
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array of frames.")
    if len(data) == 0:
        raise ValueError("JSON array is empty — nothing to display.")

    all_warns: list[str] = []
    frames: list[list[dict]] = []
    for i, raw in enumerate(data):
        targets, warns = _parse_frame(raw, i)
        all_warns.extend(warns)
        if targets is not None:
            frames.append(targets)

    if not frames:
        raise ValueError("No valid frames found after parsing.")
    return frames, all_warns


def _target_active(t: dict) -> bool:
    """A target slot is considered active if it has a non-zero position."""
    return not (t["x"] == 0.0 and t["y"] == 0.0 and t["dist_res"] == 0.0)


# ── Main Window ───────────────────────────────────────────────────────────────
class Radar2450Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LD2450 Radar Monitor")
        self.setMinimumSize(900, 720)
        self.setStyleSheet("background-color:#0a0e1a; color:#c8d6e5;")

        self.frames:   list[list[dict]] = []
        self.cursor:   int              = 0
        self._playing: bool             = False

        # Per-target position trails (deques of (x, y))
        self.trails = [deque(maxlen=TRAIL_LEN) for _ in range(3)]

        self._build_ui()
        self._set_controls_enabled(False)

        self.timer = QTimer()
        self.timer.timeout.connect(self._advance)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 8)
        main.setSpacing(8)

        # Header row
        hdr = QHBoxLayout()
        title = QLabel("LD2450  //  2D RADAR MONITOR")
        title.setStyleSheet(
            "color:#00f5d4; font-size:15px; font-weight:bold;"
            "font-family:'Courier New'; letter-spacing:3px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        self.open_btn = QPushButton("📂  Open JSON File")
        self.open_btn.setStyleSheet(BTN_STYLE)
        self.open_btn.clicked.connect(self._open_file)
        hdr.addWidget(self.open_btn)
        main.addLayout(hdr)

        # ── 2D plot ───────────────────────────────────────────────────────────
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "X (mm)")
        self.plot.setLabel("left",   "Y (mm)")
        self.plot.setStyleSheet(f"border:1px solid {BORDER_CLR}; border-radius:6px;")
        self.plot.getPlotItem().titleLabel.setText(
            '<span style="color:#6b7fa3;font-size:10pt;font-family:Courier New">'
            'Target Positions</span>'
        )
        # Radar origin marker
        self.plot.plot([0], [0], symbol="+", symbolSize=14,
                       symbolPen=pg.mkPen("#ffffff", width=2), pen=None)

        # Trail lines + current-position dots + vector arrows per target
        self.trail_curves = []
        self.dot_items     = []
        self.arrow_items   = []   # ArrowItem, one per target (hidden when not used)

        for i, color in enumerate(TARGET_COLORS):
            # Trail
            trail = self.plot.plot(
                [], [], pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DotLine)
            )
            self.trail_curves.append(trail)

            # Current position dot
            dot = self.plot.plot(
                [], [], symbol="o", symbolSize=10,
                symbolBrush=pg.mkBrush(color),
                symbolPen=pg.mkPen("#0a0e1a", width=1),
                pen=None,
            )
            self.dot_items.append(dot)

            # Velocity arrow (ArrowItem — positioned manually each frame)
            arrow = ArrowItem(
                angle=0, tipAngle=30, baseAngle=20,
                headLen=14, tailLen=20, tailWidth=3,
                brush=pg.mkBrush(color),
                pen=pg.mkPen(color, width=1),
            )
            arrow.setVisible(False)
            self.plot.addItem(arrow)
            self.arrow_items.append(arrow)

        main.addWidget(self.plot, stretch=1)

        # ── Target info cards ─────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        self.target_cards = []
        for i, color in enumerate(TARGET_COLORS):
            card, labels = self._target_card(f"TARGET {i+1}", color)
            self.target_cards.append(labels)
            cards_row.addWidget(card)
        main.addLayout(cards_row)

        # ── Options row ───────────────────────────────────────────────────────
        opts = QHBoxLayout()
        opts.setSpacing(16)

        self.show_vectors_cb = QCheckBox("Show velocity vectors")
        self.show_vectors_cb.setChecked(True)
        self.show_vectors_cb.setStyleSheet(
            "color:#c8d6e5; font-family:'Courier New'; font-size:12px;"
        )
        self.show_vectors_cb.stateChanged.connect(self._on_vector_toggle)

        self.show_trail_cb = QCheckBox("Show trail")
        self.show_trail_cb.setChecked(True)
        self.show_trail_cb.setStyleSheet(
            "color:#c8d6e5; font-family:'Courier New'; font-size:12px;"
        )
        self.show_trail_cb.stateChanged.connect(self._on_trail_toggle)

        opts.addWidget(self.show_vectors_cb)
        opts.addWidget(self.show_trail_cb)
        opts.addStretch()
        main.addLayout(opts)

        # ── Playback controls ─────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self.prev_btn = QPushButton("◀ Prev")
        self.play_btn = QPushButton("▶ Play")
        self.next_btn = QPushButton("Next ▶")
        for btn in (self.prev_btn, self.play_btn, self.next_btn):
            btn.setStyleSheet(BTN_STYLE)
            btn.setFixedHeight(30)
        self.prev_btn.clicked.connect(self._step_back)
        self.play_btn.clicked.connect(self._toggle_play)
        self.next_btn.clicked.connect(self._step_forward)

        spd_lbl = QLabel("Speed:")
        spd_lbl.setStyleSheet("font-family:'Courier New'; font-size:12px;")
        self.speed_box = QComboBox()
        self.speed_box.setStyleSheet(
            f"background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
            "border-radius:4px; padding:2px 6px; font-family:'Courier New'; font-size:12px;"
        )
        self.speed_box.addItems(list(SPEEDS.keys()))
        self.speed_box.setCurrentText("1×")
        self.speed_box.currentTextChanged.connect(self._on_speed_change)

        self.frame_lbl = QLabel("Frame — / —")
        self.frame_lbl.setStyleSheet(
            "font-family:'Courier New'; font-size:12px; color:#6b7fa3;"
        )

        ctrl.addWidget(self.prev_btn)
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.next_btn)
        ctrl.addSpacing(16)
        ctrl.addWidget(spd_lbl)
        ctrl.addWidget(self.speed_box)
        ctrl.addStretch()
        ctrl.addWidget(self.frame_lbl)
        main.addLayout(ctrl)

        # Slider
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.valueChanged.connect(self._on_slider)
        main.addWidget(self.slider)

        # Status bar
        self.status = QStatusBar()
        self.status.setStyleSheet(
            "font-family:'Courier New'; font-size:11px; color:#6b7fa3;"
            f"background:{PANEL_BG}; border-top:1px solid {BORDER_CLR};"
        )
        self.setStatusBar(self.status)
        self.status.showMessage("No file loaded — use 📂 Open JSON File to begin.")

    def _target_card(self, title: str, color: str):
        frame = QFrame()
        frame.setStyleSheet(
            f"background:{PANEL_BG}; border:1px solid {BORDER_CLR}; border-radius:6px;"
        )
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(10, 8, 10, 8)
        vbox.setSpacing(3)

        hdr = QLabel(title)
        hdr.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:bold; font-family:'Courier New';"
        )
        vbox.addWidget(hdr)

        labels = {}
        for key, display in [("x", "X (mm)"), ("y", "Y (mm)"),
                              ("speed", "Speed (cm/s)"), ("dist_res", "Dist Res (mm)"),
                              ("vel", "Δ Velocity (mm/f)")]:
            row = QHBoxLayout()
            row.setSpacing(4)
            k_lbl = QLabel(f"{display}:")
            k_lbl.setStyleSheet("color:#6b7fa3; font-size:10px; font-family:'Courier New';")
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet(
                f"color:#c8d6e5; font-size:10px; font-family:'Courier New';"
            )
            row.addWidget(k_lbl)
            row.addStretch()
            row.addWidget(v_lbl)
            vbox.addLayout(row)
            labels[key] = v_lbl

        return frame, labels

    # ── File loading ──────────────────────────────────────────────────────────
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open LD2450 JSON File", "",
            "JSON Files (*.json);;All Files (*)"
        )
        if path:
            self._load(path)

    def _load(self, path: str):
        self.timer.stop()
        self._playing = False
        self.play_btn.setText("▶ Play")

        try:
            frames, warnings = _load_json_file(path)
        except (FileNotFoundError, ValueError, OSError) as e:
            QMessageBox.critical(self, "Failed to Load File", str(e))
            self.status.showMessage(f"⚠  {e}")
            return

        self.frames = frames
        self.cursor = 0
        self._reset_trails()

        self.slider.blockSignals(True)
        self.slider.setMaximum(len(frames) - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)

        self._set_controls_enabled(True)

        # Auto-fit plot to data extents
        self._autorange()

        name = Path(path).name
        msg = f"Loaded {len(frames)} frames from {name}"
        if warnings:
            msg += f"  |  ⚠ {len(warnings)} warning(s)"
            detail = "\n".join(warnings[:30])
            if len(warnings) > 30:
                detail += f"\n… and {len(warnings)-30} more."
            QMessageBox.warning(self, "Parse Warnings", detail)
        self.status.showMessage(msg)
        self.setWindowTitle(f"LD2450 Radar Monitor — {name}")

        self._render_frame(0, prev_frame=None)

    def _autorange(self):
        """Fit the plot view to the bounding box of all positions in the file."""
        xs, ys = [], []
        for frame in self.frames:
            for t in frame:
                if _target_active(t):
                    xs.append(t["x"])
                    ys.append(t["y"])
        if not xs:
            return
        pad = 100   # mm padding
        self.plot.setXRange(min(xs) - pad, max(xs) + pad, padding=0)
        self.plot.setYRange(min(ys) - pad, max(ys) + pad, padding=0)

    # ── Trail management ──────────────────────────────────────────────────────
    def _reset_trails(self):
        for trail in self.trails:
            trail.clear()

    def _rebuild_trails_up_to(self, idx: int):
        self._reset_trails()
        start = max(0, idx - TRAIL_LEN + 1)
        for i in range(start, idx + 1):
            for t_idx, target in enumerate(self.frames[i]):
                if _target_active(target):
                    self.trails[t_idx].append((target["x"], target["y"]))
                else:
                    self.trails[t_idx].clear()   # gap resets trail

    # ── Playback ──────────────────────────────────────────────────────────────
    def _toggle_play(self):
        if not self.frames:
            return
        if self._playing:
            self._pause()
        else:
            if self.cursor >= len(self.frames) - 1:
                self.cursor = 0
                self._reset_trails()
                self.slider.blockSignals(True)
                self.slider.setValue(0)
                self.slider.blockSignals(False)
            self._play()

    def _play(self):
        self._playing = True
        self.play_btn.setText("⏸ Pause")
        self.timer.start(SPEEDS.get(self.speed_box.currentText(), 100))

    def _pause(self):
        self._playing = False
        self.play_btn.setText("▶ Play")
        self.timer.stop()

    def _advance(self):
        if self.cursor >= len(self.frames) - 1:
            self._pause()
            self.status.showMessage(f"Playback complete — {len(self.frames)} frames.")
            return
        prev = self.frames[self.cursor]
        self.cursor += 1
        self.slider.blockSignals(True)
        self.slider.setValue(self.cursor)
        self.slider.blockSignals(False)
        self._render_frame(self.cursor, prev_frame=prev)

    def _step_back(self):
        if not self.frames or self.cursor == 0:
            return
        self._pause()
        self.cursor -= 1
        self._rebuild_trails_up_to(self.cursor)
        prev = self.frames[self.cursor - 1] if self.cursor > 0 else None
        self.slider.blockSignals(True)
        self.slider.setValue(self.cursor)
        self.slider.blockSignals(False)
        self._render_frame(self.cursor, prev_frame=prev, trails_ready=True)

    def _step_forward(self):
        if not self.frames or self.cursor >= len(self.frames) - 1:
            return
        self._pause()
        prev = self.frames[self.cursor]
        self.cursor += 1
        self.slider.blockSignals(True)
        self.slider.setValue(self.cursor)
        self.slider.blockSignals(False)
        self._render_frame(self.cursor, prev_frame=prev)

    def _on_slider(self, value: int):
        if not self.frames:
            return
        self._pause()
        self.cursor = value
        self._rebuild_trails_up_to(value)
        prev = self.frames[value - 1] if value > 0 else None
        self._render_frame(value, prev_frame=prev, trails_ready=True)

    def _on_speed_change(self, text: str):
        if self._playing:
            self.timer.setInterval(SPEEDS.get(text, 100))

    def _on_vector_toggle(self):
        if not self.frames:
            return
        # Re-render current frame to show/hide arrows
        prev = self.frames[self.cursor - 1] if self.cursor > 0 else None
        self._render_frame(self.cursor, prev_frame=prev, trails_ready=True)

    def _on_trail_toggle(self):
        show = self.show_trail_cb.isChecked()
        for curve in self.trail_curves:
            curve.setVisible(show)

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render_frame(self, idx: int, prev_frame, trails_ready: bool = False):
        frame = self.frames[idx]

        # Update trails (unless caller already rebuilt them)
        if not trails_ready:
            for t_idx, target in enumerate(frame):
                if _target_active(target):
                    self.trails[t_idx].append((target["x"], target["y"]))
                else:
                    self.trails[t_idx].clear()

        show_vec   = self.show_vectors_cb.isChecked()
        show_trail = self.show_trail_cb.isChecked()

        for t_idx, target in enumerate(frame):
            active = _target_active(target)
            x, y   = target["x"], target["y"]

            # Trail
            trail_pts = list(self.trails[t_idx])
            if active and show_trail and len(trail_pts) > 1:
                tx = [p[0] for p in trail_pts]
                ty = [p[1] for p in trail_pts]
                self.trail_curves[t_idx].setData(tx, ty)
                self.trail_curves[t_idx].setVisible(True)
            else:
                self.trail_curves[t_idx].setData([], [])

            # Current dot
            if active:
                self.dot_items[t_idx].setData([x], [y])
            else:
                self.dot_items[t_idx].setData([], [])

            # Velocity vector arrow
            arrow = self.arrow_items[t_idx]
            dx = dy = 0.0
            if active and show_vec and prev_frame is not None:
                prev = prev_frame[t_idx]
                if _target_active(prev):
                    dx = (x - prev["x"]) * VECTOR_SCALE
                    dy = (y - prev["y"]) * VECTOR_SCALE

            if active and show_vec and (dx != 0.0 or dy != 0.0):
                angle_deg = math.degrees(math.atan2(dy, dx))
                arrow.setStyle(angle=180 - angle_deg)   # ArrowItem angle is CW from right
                arrow.setPos(x, y)
                arrow.setVisible(True)
            else:
                arrow.setVisible(False)

            # Info card
            cards = self.target_cards[t_idx]
            if active:
                cards["x"].setText(f"{x:.0f}")
                cards["y"].setText(f"{y:.0f}")
                cards["speed"].setText(f"{target['speed']:.1f}")
                cards["dist_res"].setText(f"{target['dist_res']:.0f}")
                mag = math.hypot(dx / VECTOR_SCALE, dy / VECTOR_SCALE) if (dx or dy) else 0
                cards["vel"].setText(f"{mag:.1f}" if prev_frame else "—")
            else:
                for v in cards.values():
                    v.setText("—")

        self.frame_lbl.setText(f"Frame {idx + 1} / {len(self.frames)}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _set_controls_enabled(self, enabled: bool):
        for w in (self.prev_btn, self.play_btn, self.next_btn,
                  self.slider, self.speed_box,
                  self.show_vectors_cb, self.show_trail_cb):
            w.setEnabled(enabled)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Radar2450Window()
    win.show()

    if len(sys.argv) > 1:
        win._load(sys.argv[1])

    sys.exit(app.exec())
