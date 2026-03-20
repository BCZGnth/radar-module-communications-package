"""
LD2450 Radar File Player
────────────────────────
Main window  — 2D animated scatter with trail + velocity vectors + slider playback
XY window    — static X-vs-frame and Y-vs-frame plots for all 3 targets
              (opens via "Show X/Y Plots" button, updates whenever a file is loaded)

Frame format: 12-element JSON array
  [x1, y1, speed1, res1,  x2, y2, speed2, res2,  x3, y3, speed3, res3]
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

TARGET_COLORS = ["#00f5d4", "#f5a623", "#e84393"]
TRAIL_LEN     = 20
VECTOR_SCALE  = 8.0
PANEL_BG      = "#111827"
BORDER_CLR    = "#1e2d3d"

BTN_STYLE = (
    "QPushButton {"
    f"  background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
    "  border-radius:4px; padding:4px 10px; font-family:'Courier New'; font-size:12px;"
    "}"
    "QPushButton:hover    { background:#1e2d3d; }"
    "QPushButton:pressed  { background:#0a0e1a; }"
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
    warns = []
    if not isinstance(raw, (list, tuple)):
        return None, [f"Frame {idx}: not a list — skipped."]
    if len(raw) < 12:
        warns.append(f"Frame {idx}: expected 12 values, got {len(raw)} — padding with 0.")
        raw = list(raw) + [0] * (12 - len(raw))
    targets = []
    for t in range(3):
        b = t * 4
        try:
            targets.append({
                "x":     float(raw[b]),
                "y":     float(raw[b + 1]),
                "speed": float(raw[b + 2]),
                "res":   float(raw[b + 3]),
            })
        except (TypeError, ValueError) as e:
            warns.append(f"Frame {idx} target {t+1}: bad value ({e}) — defaulted to 0.")
            targets.append({"x": 0.0, "y": 0.0, "speed": 0.0, "res": 0.0})
    return targets, warns


def _load_json_file(path: str) -> tuple[list[list[dict]], list[str]]:
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
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], (int, float)):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array of frames.")
    if len(data) == 0:
        raise ValueError("JSON array is empty — nothing to display.")

    all_warns, frames = [], []
    for i, raw in enumerate(data):
        targets, warns = _parse_frame(raw, i)
        all_warns.extend(warns)
        if targets is not None:
            frames.append(targets)
    if not frames:
        raise ValueError("No valid frames found after parsing.")
    return frames, all_warns


def _active(t: dict) -> bool:
    return not (t["x"] == 0.0 and t["y"] == 0.0 and t["res"] == 0.0)


def make_pen(color, width=2):
    return pg.mkPen(color=color, width=width)


# ── XY Time-Series Window ─────────────────────────────────────────────────────
class XYWindow(QMainWindow):
    """
    Side-by-side layout:
      Left  — X plot:  horizontal axis = X (mm),  vertical axis = Frame (time)
              i.e. the plot is "rotated" — time runs top-to-bottom on the Y axis.
      Right — Y plot:  horizontal axis = Frame (time),  vertical axis = Y (mm)

    The time axes are linked: panning/zooming one moves the other.
    A horizontal cursor on the X plot and a vertical cursor on the Y plot both
    track the current playback frame.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("X / Y vs Frame")
        self.setMinimumSize(900, 520)
        self.setStyleSheet("background-color:#0a0e1a; color:#c8d6e5;")
        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # Title + legend row
        hdr = QHBoxLayout()
        title = QLabel("X / Y  vs  Frame Index")
        title.setStyleSheet(
            "color:#00f5d4; font-size:14px; font-weight:bold;"
            "font-family:'Courier New'; letter-spacing:2px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        for i, color in enumerate(TARGET_COLORS):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:14px;")
            lbl = QLabel(f"Target {i+1}")
            lbl.setStyleSheet("font-family:'Courier New'; font-size:11px; color:#8899aa;")
            hdr.addWidget(dot)
            hdr.addWidget(lbl)
            if i < 2:
                hdr.addSpacing(12)
        outer.addLayout(hdr)

        # ── Side-by-side plots ────────────────────────────────────────────────
        plots_row = QHBoxLayout()
        plots_row.setSpacing(8)

        # LEFT — X plot (rotated: X mm on horiz, frame on vert)
        self.x_plot = pg.PlotWidget()
        self.x_plot.setLabel("bottom", "X (mm)")
        self.x_plot.setLabel("left",   "Frame")
        # self.x_plot.invertY(True)
        self.x_plot.showGrid(x=True, y=True, alpha=0.15)
        self.x_plot.setStyleSheet(f"border:1px solid {BORDER_CLR}; border-radius:6px;")
        self.x_plot.getPlotItem().titleLabel.setText(
            '<span style="color:#6b7fa3;font-size:9pt;font-family:Courier New">'
            'X Position  (time ↕)</span>'
        )
        # Invert Y so frame 0 is at top and time flows downward
        # self.x_plot.invertY(True)

        # RIGHT — Y plot (standard: frame on horiz, Y mm on vert)
        self.y_plot = pg.PlotWidget()
        self.y_plot.setLabel("bottom", "Frame")
        self.y_plot.setLabel("left",   "Y (mm)")
        self.y_plot.invertY(True)
        self.y_plot.showGrid(x=True, y=True, alpha=0.15)
        self.y_plot.setStyleSheet(f"border:1px solid {BORDER_CLR}; border-radius:6px;")
        self.y_plot.getPlotItem().titleLabel.setText(
            '<span style="color:#6b7fa3;font-size:9pt;font-family:Courier New">'
            'Y Position  (time →)</span>'
        )

        # Link time axes:
        #   x_plot Y-axis  ↔  y_plot X-axis  (both represent frame index)
        # pyqtgraph only supports linking same-orientation axes, so we use a
        # ViewBox-level trick: keep them in sync manually via rangeChanged signals.
        self.x_plot.getViewBox().sigYRangeChanged.connect(self._sync_x_to_y)
        self.y_plot.getViewBox().sigXRangeChanged.connect(self._sync_y_to_x)
        self._syncing = False   # re-entrancy guard

        plots_row.addWidget(self.x_plot, stretch=1)
        plots_row.addWidget(self.y_plot, stretch=2)
        outer.addLayout(plots_row, stretch=1)

        # Data curves — x_plot: setData(x_mm, frame);  y_plot: setData(frame, y_mm)
        self.x_curves = []
        self.y_curves = []
        for color in TARGET_COLORS:
            self.x_curves.append(self.x_plot.plot(pen=make_pen(color)))
            self.y_curves.append(self.y_plot.plot(pen=make_pen(color)))

        # Cursors
        cursor_pen = pg.mkPen(color="#ffffff", width=1, style=Qt.PenStyle.DashLine)
        # X plot: horizontal line (angle=0) marks the current frame on the Y axis
        self.x_cursor = pg.InfiniteLine(angle=0, movable=False, pen=cursor_pen)
        # Y plot: vertical line (angle=90) marks the current frame on the X axis
        self.y_cursor = pg.InfiniteLine(angle=90, movable=False, pen=cursor_pen)
        self.x_plot.addItem(self.x_cursor)
        self.y_plot.addItem(self.y_cursor)

    # ── Axis sync ─────────────────────────────────────────────────────────────
    def _sync_x_to_y(self, vb, range_):
        """x_plot Y range changed → push to y_plot X range."""
        if self._syncing:
            return
        self._syncing = True
        self.y_plot.setXRange(range_[0], range_[1], padding=0)
        self._syncing = False

    def _sync_y_to_x(self, vb, range_):
        """y_plot X range changed → push to x_plot Y range."""
        if self._syncing:
            return
        self._syncing = True
        self.x_plot.setYRange(range_[0], range_[1], padding=0)
        self._syncing = False

    # ── Data ──────────────────────────────────────────────────────────────────
    def load(self, frames: list[list[dict]]):
        """Populate both plots with all frame data. Called once per file load."""
        n = len(frames)
        t = np.arange(n, dtype=float)

        for t_idx in range(3):
            xs = np.array([
                frames[i][t_idx]["x"] if _active(frames[i][t_idx]) else np.nan
                for i in range(n)
            ])
            ys = np.array([
                frames[i][t_idx]["y"] if _active(frames[i][t_idx]) else np.nan
                for i in range(n)
            ])
            # X plot: x=X(mm), y=frame  → time on vertical axis
            self.x_curves[t_idx].setData(xs, t)
            # Y plot: x=frame, y=Y(mm)  → time on horizontal axis
            self.y_curves[t_idx].setData(t, ys)

        self.x_plot.autoRange()
        self.y_plot.autoRange()
        self.set_cursor(0)

    def set_cursor(self, frame_idx: int):
        self.x_cursor.setPos(frame_idx)   # horizontal line on x_plot Y axis
        self.y_cursor.setPos(frame_idx)   # vertical line on y_plot X axis

    def closeEvent(self, event):
        self.hide()
        event.ignore()


# ── Main 2D Player Window ────────────────────────────────────────────────────
class Radar2450Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LD2450 Radar File Player")
        self.setMinimumSize(950, 740)
        self.setStyleSheet("background-color:#0a0e1a; color:#c8d6e5;")

        self.frames:   list[list[dict]] = []
        self.cursor:   int              = 0
        self._playing: bool             = False
        self.trails = [deque(maxlen=TRAIL_LEN) for _ in range(3)]

        self._xy_window = XYWindow()

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

        # Header
        hdr = QHBoxLayout()
        title = QLabel("LD2450  //  2D RADAR PLAYER")
        title.setStyleSheet(
            "color:#00f5d4; font-size:15px; font-weight:bold;"
            "font-family:'Courier New'; letter-spacing:3px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self.xy_btn = QPushButton("📈  Show X/Y Plots")
        self.xy_btn.setStyleSheet(BTN_STYLE)
        self.xy_btn.clicked.connect(self._show_xy)

        self.open_btn = QPushButton("📂  Open JSON File")
        self.open_btn.setStyleSheet(BTN_STYLE)
        self.open_btn.clicked.connect(self._open_file)

        hdr.addWidget(self.xy_btn)
        hdr.addWidget(self.open_btn)
        main.addLayout(hdr)

        # 2D plot
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
        self.plot.plot([0], [0], symbol="+", symbolSize=14,
                       symbolPen=pg.mkPen("#ffffff", width=2), pen=None)

        self.trail_curves, self.dot_items, self.arrow_items = [], [], []
        for i, color in enumerate(TARGET_COLORS):
            self.trail_curves.append(self.plot.plot(
                [], [], pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DotLine)
            ))
            self.dot_items.append(self.plot.plot(
                [], [], symbol="o", symbolSize=10,
                symbolBrush=pg.mkBrush(color),
                symbolPen=pg.mkPen("#0a0e1a", width=1), pen=None,
            ))
            arrow = ArrowItem(
                angle=0, tipAngle=30, baseAngle=20,
                headLen=14, tailLen=20, tailWidth=3,
                brush=pg.mkBrush(color), pen=pg.mkPen(color, width=1),
            )
            arrow.setVisible(False)
            self.plot.addItem(arrow)
            self.arrow_items.append(arrow)

        main.addWidget(self.plot, stretch=1)

        # Target cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        self.target_cards = []
        for i, color in enumerate(TARGET_COLORS):
            card, labels = self._target_card(f"TARGET {i+1}", color)
            self.target_cards.append(labels)
            cards_row.addWidget(card)
        main.addLayout(cards_row)

        # Options
        opts = QHBoxLayout()
        opts.setSpacing(16)
        self.show_vectors_cb = QCheckBox("Velocity vectors")
        self.show_vectors_cb.setChecked(True)
        self.show_vectors_cb.setStyleSheet(
            "color:#c8d6e5; font-family:'Courier New'; font-size:12px;"
        )
        self.show_trail_cb = QCheckBox("Trail")
        self.show_trail_cb.setChecked(True)
        self.show_trail_cb.setStyleSheet(
            "color:#c8d6e5; font-family:'Courier New'; font-size:12px;"
        )
        self.show_trail_cb.stateChanged.connect(self._on_trail_toggle)
        opts.addWidget(self.show_vectors_cb)
        opts.addWidget(self.show_trail_cb)
        opts.addStretch()
        main.addLayout(opts)

        # Playback controls
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

    def _target_card(self, title, color):
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
                              ("speed", "Speed (cm/s)"), ("res", "Dist Res (mm)"),
                              ("vel", "Δ Pos (mm/f)")]:
            row = QHBoxLayout()
            k = QLabel(f"{display}:")
            k.setStyleSheet("color:#6b7fa3; font-size:10px; font-family:'Courier New';")
            v = QLabel("—")
            v.setStyleSheet("color:#c8d6e5; font-size:10px; font-family:'Courier New';")
            row.addWidget(k); row.addStretch(); row.addWidget(v)
            vbox.addLayout(row)
            labels[key] = v
        return frame, labels

    # ── XY window ─────────────────────────────────────────────────────────────
    def _show_xy(self):
        self._xy_window.show()
        self._xy_window.raise_()
        self._xy_window.activateWindow()

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
        self.setWindowTitle(f"LD2450 Radar Player — {name}")

        # Push full dataset to XY window
        self._xy_window.load(frames)
        self._xy_window.setWindowTitle(f"X / Y vs Frame — {name}")

        self._render_frame(0, prev_frame=None)

    def _autorange(self):
        xs, ys = [], []
        for frame in self.frames:
            for t in frame:
                if _active(t):
                    xs.append(t["x"]); ys.append(t["y"])
        if not xs:
            return
        pad = 100
        self.plot.setXRange(min(xs) - pad, max(xs) + pad, padding=0)
        self.plot.setYRange(min(ys) - pad, max(ys) + pad, padding=0)

    # ── Trails ────────────────────────────────────────────────────────────────
    def _reset_trails(self):
        for t in self.trails:
            t.clear()

    def _rebuild_trails_up_to(self, idx: int):
        self._reset_trails()
        for i in range(max(0, idx - TRAIL_LEN + 1), idx + 1):
            for t_idx, tgt in enumerate(self.frames[i]):
                if _active(tgt):
                    self.trails[t_idx].append((tgt["x"], tgt["y"]))
                else:
                    self.trails[t_idx].clear()

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

    def _on_trail_toggle(self, state):
        if not state:
            for c in self.trail_curves:
                c.setData([], [])

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render_frame(self, idx: int, prev_frame, trails_ready: bool = False):
        frame = self.frames[idx]
        show_vec   = self.show_vectors_cb.isChecked()
        show_trail = self.show_trail_cb.isChecked()

        if not trails_ready:
            for t_idx, tgt in enumerate(frame):
                if _active(tgt):
                    self.trails[t_idx].append((tgt["x"], tgt["y"]))
                else:
                    self.trails[t_idx].clear()

        for t_idx, tgt in enumerate(frame):
            active = _active(tgt)
            x, y   = tgt["x"], tgt["y"]

            # Trail
            pts = list(self.trails[t_idx])
            if active and show_trail and len(pts) > 1:
                self.trail_curves[t_idx].setData([p[0] for p in pts], [p[1] for p in pts])
                self.trail_curves[t_idx].setVisible(True)
            else:
                self.trail_curves[t_idx].setData([], [])

            # Dot
            self.dot_items[t_idx].setData([x], [y]) if active else self.dot_items[t_idx].setData([], [])

            # Velocity arrow
            dx = dy = 0.0
            arrow = self.arrow_items[t_idx]
            if active and show_vec and prev_frame is not None:
                prev = prev_frame[t_idx]
                if _active(prev):
                    dx = (x - prev["x"]) * VECTOR_SCALE
                    dy = (y - prev["y"]) * VECTOR_SCALE
            if active and show_vec and (dx or dy):
                arrow.setStyle(angle=180 - math.degrees(math.atan2(dy, dx)))
                arrow.setPos(x, y)
                arrow.setVisible(True)
            else:
                arrow.setVisible(False)

            # Cards
            cards = self.target_cards[t_idx]
            if active:
                mag = math.hypot(dx / VECTOR_SCALE, dy / VECTOR_SCALE) if (dx or dy) else 0
                cards["x"].setText(f"{x:.0f}")
                cards["y"].setText(f"{y:.0f}")
                cards["speed"].setText(f"{tgt['speed']:.1f}")
                cards["res"].setText(f"{tgt['res']:.0f}")
                cards["vel"].setText(f"{mag:.1f}" if prev_frame else "—")
            else:
                for v in cards.values():
                    v.setText("—")

        self.frame_lbl.setText(f"Frame {idx + 1} / {len(self.frames)}")

        # Update cursor line in XY window if it is visible
        if self._xy_window.isVisible():
            self._xy_window.set_cursor(idx)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _set_controls_enabled(self, enabled: bool):
        for w in (self.prev_btn, self.play_btn, self.next_btn,
                  self.slider, self.speed_box,
                  self.show_vectors_cb, self.show_trail_cb, self.xy_btn):
            w.setEnabled(enabled)

    def closeEvent(self, event):
        self.timer.stop()
        self._xy_window.close()   # actually close child on main exit
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Radar2450Window()
    win.show()

    if len(sys.argv) > 1:
        win._load(sys.argv[1])

    sys.exit(app.exec())
