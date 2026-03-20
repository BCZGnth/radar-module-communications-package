"""
LD2410C Radar Grapher
─────────────────────
• Open a JSON file (array of frames) via File menu or toolbar button
• Playback controls: Play / Pause, Step ◀ ▶, speed selector
• Slider scrubs through frames manually
• Rolling history charts update as if data is arriving live
• Full error handling for bad files, missing keys, empty arrays
"""

import sys
import json
from collections import deque
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QSlider, QFileDialog, QMessageBox,
    QComboBox, QStatusBar,
)
from PyQt6.QtCore import QTimer, Qt
import pyqtgraph as pg
import numpy as np

# ── Theme ─────────────────────────────────────────────────────────────────────
pg.setConfigOption("background", "#0a0e1a")
pg.setConfigOption("foreground", "#c8d6e5")

ACCENT_MOVE   = "#00f5d4"
ACCENT_STATIC = "#f5a623"
ACCENT_DETECT = "#e84393"
GATE_MOVE_CLR = "#00c4aa"
GATE_STAT_CLR = "#d4890a"
PANEL_BG      = "#111827"
BORDER_CLR    = "#1e2d3d"

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
    f"QSlider::handle:horizontal {{ background:{ACCENT_MOVE}; width:14px; height:14px;"
    "  margin:-5px 0; border-radius:7px; }"
    f"QSlider::sub-page:horizontal {{ background:{ACCENT_MOVE}; border-radius:2px; }}"
)

HISTORY = 120
SPEEDS  = {"0.25×": 400, "0.5×": 200, "1×": 100, "2×": 50, "4×": 25}
REQUIRED_KEYS = {"target", "move_dist", "move_energy", "static_dist",
                 "static_energy", "detect_distance"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def make_pen(color, width=2):
    return pg.mkPen(color=color, width=width)


def _coerce_frame(raw: dict, idx: int) -> tuple[dict | None, list[str]]:
    """Sanitise one raw frame. Returns (frame_dict, warnings). None = skip frame."""
    warnings = []
    if not isinstance(raw, dict):
        return None, [f"Frame {idx}: not an object — skipped."]

    frame = {}
    frame["target"] = str(raw.get("target", "—"))

    for key in ("move_dist", "move_energy", "static_dist",
                "static_energy", "detect_distance"):
        val = raw.get(key)
        if val is None:
            warnings.append(f"Frame {idx}: missing '{key}' — defaulted to 0.")
            frame[key] = 0.0
        else:
            try:
                frame[key] = float(val)
            except (TypeError, ValueError):
                warnings.append(f"Frame {idx}: '{key}' = {val!r} is not numeric — defaulted to 0.")
                frame[key] = 0.0

    for i in range(9):
        for prefix in ("move_gate", "stationary_gate"):
            k = f"{prefix}_{i}_energy"
            val = raw.get(k, 0)
            try:
                frame[k] = float(val)
            except (TypeError, ValueError):
                warnings.append(f"Frame {idx}: '{k}' is not numeric — defaulted to 0.")
                frame[k] = 0.0

    return frame, warnings


def _load_json_file(path: str) -> tuple[list[dict], list[str]]:
    """
    Parse a JSON file into sanitised frame dicts.
    Returns (frames, warnings).
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
            raise ValueError(f"Invalid JSON — could not parse file.\n\nDetail: {e}") from e

    # Accept a lone dict as a single-frame file
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        raise ValueError(
            "JSON root must be an array of frame objects (or a single frame object)."
        )

    if len(data) == 0:
        raise ValueError("JSON array is empty — nothing to display.")

    all_warnings: list[str] = []
    frames: list[dict] = []
    for i, raw in enumerate(data):
        frame, warns = _coerce_frame(raw, i)
        all_warnings.extend(warns)
        if frame is not None:
            frames.append(frame)

    if not frames:
        raise ValueError("No valid frames found after parsing the file.")

    return frames, all_warnings


# ── Main Window ───────────────────────────────────────────────────────────────
class RadarWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LD2410C Radar Monitor")
        self.setMinimumSize(1150, 820)
        self.setStyleSheet("background-color:#0a0e1a; color:#c8d6e5;")

        # Data
        self.frames:   list[dict] = []
        self.cursor:   int        = 0
        self._playing: bool       = False

        # Rolling history buffers
        self.t             = deque([0] * HISTORY, maxlen=HISTORY)
        self.move_dist_h   = deque([0] * HISTORY, maxlen=HISTORY)
        self.static_dist_h = deque([0] * HISTORY, maxlen=HISTORY)
        self.detect_dist_h = deque([0] * HISTORY, maxlen=HISTORY)
        self.move_energy_h = deque([0] * HISTORY, maxlen=HISTORY)
        self.stat_energy_h = deque([0] * HISTORY, maxlen=HISTORY)
        self.tick = 0

        self._build_ui()
        self._set_controls_enabled(False)

        self.timer = QTimer()
        self.timer.timeout.connect(self._advance)

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 8)
        main.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("LD2410C  //  RADAR MONITOR")
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

        # Stat cards
        card_row = QHBoxLayout()
        card_row.setSpacing(8)
        self.card_target = self._stat_card("TARGET",           "#e84393")
        self.card_move_d = self._stat_card("MOVE DIST (cm)",   ACCENT_MOVE)
        self.card_move_e = self._stat_card("MOVE ENERGY",      ACCENT_MOVE)
        self.card_stat_d = self._stat_card("STATIC DIST (cm)", ACCENT_STATIC)
        self.card_stat_e = self._stat_card("STATIC ENERGY",    ACCENT_STATIC)
        self.card_detect = self._stat_card("DETECT DIST (cm)", ACCENT_DETECT)
        for fw, _ in (self.card_target, self.card_move_d, self.card_move_e,
                      self.card_stat_d, self.card_stat_e, self.card_detect):
            card_row.addWidget(fw)
        main.addLayout(card_row)

        # Rolling charts
        mid = QHBoxLayout()
        mid.setSpacing(8)
        self.dist_plot   = self._make_plot("Distance over Time (cm)", y_range=(0, 900))
        self.energy_plot = self._make_plot("Energy over Time",         y_range=(0, 110))
        self.curve_move_d = self.dist_plot.plot(pen=make_pen(ACCENT_MOVE))
        self.curve_stat_d = self.dist_plot.plot(pen=make_pen(ACCENT_STATIC))
        self.curve_det_d  = self.dist_plot.plot(pen=make_pen(ACCENT_DETECT, 1))
        self.curve_move_e = self.energy_plot.plot(pen=make_pen(ACCENT_MOVE))
        self.curve_stat_e = self.energy_plot.plot(pen=make_pen(ACCENT_STATIC))
        self._add_legend(self.dist_plot, [
            ("Move dist",   ACCENT_MOVE),
            ("Static dist", ACCENT_STATIC),
            ("Detect dist", ACCENT_DETECT),
        ])
        self._add_legend(self.energy_plot, [
            ("Move energy",   ACCENT_MOVE),
            ("Static energy", ACCENT_STATIC),
        ])
        mid.addWidget(self.dist_plot,   stretch=3)
        mid.addWidget(self.energy_plot, stretch=2)
        main.addLayout(mid)

        # Gate bar charts
        bot = QHBoxLayout()
        bot.setSpacing(8)
        self.move_bar_plot = self._make_plot("Move Gate Energy (gates 0–8)",       y_range=(0, 110))
        self.stat_bar_plot = self._make_plot("Stationary Gate Energy (gates 0–8)", y_range=(0, 110))
        x = np.arange(9)
        self.move_bars = pg.BarGraphItem(x=x, height=[0]*9, width=0.6,
                                          brush=GATE_MOVE_CLR, pen=pg.mkPen(None))
        self.stat_bars = pg.BarGraphItem(x=x, height=[0]*9, width=0.6,
                                          brush=GATE_STAT_CLR, pen=pg.mkPen(None))
        self.move_bar_plot.addItem(self.move_bars)
        self.stat_bar_plot.addItem(self.stat_bars)
        ticks = [(i, str(i)) for i in range(9)]
        self.move_bar_plot.getAxis("bottom").setTicks([ticks])
        self.stat_bar_plot.getAxis("bottom").setTicks([ticks])
        bot.addWidget(self.move_bar_plot)
        bot.addWidget(self.stat_bar_plot)
        main.addLayout(bot)

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
        self.frame_lbl.setStyleSheet("font-family:'Courier New'; font-size:12px; color:#6b7fa3;")

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

    # ── Widget factories ──────────────────────────────────────────────────────
    def _stat_card(self, title, color):
        frame = QFrame()
        frame.setStyleSheet(
            f"background:{PANEL_BG}; border:1px solid {BORDER_CLR}; border-radius:6px;"
        )
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(8, 6, 8, 6)
        vbox.setSpacing(2)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("color:#6b7fa3; font-size:10px; font-family:'Courier New';")
        v = QLabel("—")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setStyleSheet(
            f"color:{color}; font-size:20px; font-weight:bold; font-family:'Courier New';"
        )
        vbox.addWidget(t)
        vbox.addWidget(v)
        return frame, v

    def _make_plot(self, title, y_range=(0, 100)):
        pw = pg.PlotWidget()
        pw.setYRange(*y_range)
        pw.showGrid(x=True, y=True, alpha=0.15)
        pw.setStyleSheet(f"border:1px solid {BORDER_CLR}; border-radius:6px;")
        pw.getPlotItem().titleLabel.setText(
            f'<span style="color:#6b7fa3;font-size:9pt;font-family:Courier New">{title}</span>'
        )
        return pw

    def _add_legend(self, plot, items):
        legend = plot.addLegend(offset=(5, 5))
        legend.setLabelTextColor("#8899aa")
        for name, color in items:
            legend.addItem(pg.PlotDataItem(pen=make_pen(color)), name)

    # ── File loading ──────────────────────────────────────────────────────────
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Radar JSON File", "",
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
        self._reset_history()

        # Update slider range without triggering _on_slider
        self.slider.blockSignals(True)
        self.slider.setMaximum(len(frames) - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)

        self._set_controls_enabled(True)

        name = Path(path).name
        msg = f"Loaded {len(frames)} frames from {name}"
        if warnings:
            msg += f"  |  ⚠ {len(warnings)} warning(s) — see details"
            detail = "\n".join(warnings[:30])
            if len(warnings) > 30:
                detail += f"\n… and {len(warnings) - 30} more."
            QMessageBox.warning(self, "Parse Warnings", detail)
        self.status.showMessage(msg)
        self.setWindowTitle(f"LD2410C Radar Monitor — {name}")

        # Render first frame
        self._render_frame(0)

    # ── History ───────────────────────────────────────────────────────────────
    def _reset_history(self):
        for buf in (self.t, self.move_dist_h, self.static_dist_h,
                    self.detect_dist_h, self.move_energy_h, self.stat_energy_h):
            buf.clear()
            buf.extend([0] * HISTORY)
        self.tick = 0

    def _push_history(self, frame: dict):
        self.tick += 1
        self.t.append(self.tick)
        self.move_dist_h.append(frame.get("move_dist",         0) * 10)
        self.static_dist_h.append(frame.get("static_dist",     0) * 10)
        self.detect_dist_h.append(frame.get("detect_distance", 0) * 10)
        self.move_energy_h.append(frame.get("move_energy",     0))
        self.stat_energy_h.append(frame.get("static_energy",   0))

    # ── Playback ──────────────────────────────────────────────────────────────
    def _toggle_play(self):
        if not self.frames:
            return
        if self._playing:
            self._pause()
        else:
            if self.cursor >= len(self.frames) - 1:
                # Restart from beginning
                self.cursor = 0
                self._reset_history()
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
            self.status.showMessage(
                f"Playback complete — {len(self.frames)} frames."
            )
            return
        self.cursor += 1
        self.slider.blockSignals(True)
        self.slider.setValue(self.cursor)
        self.slider.blockSignals(False)
        self._render_frame(self.cursor)

    def _step_back(self):
        if not self.frames:
            return
        self._pause()
        if self.cursor > 0:
            self.cursor -= 1
            # Rebuild history up to new cursor
            self._reset_history()
            start = max(0, self.cursor - HISTORY + 1)
            for i in range(start, self.cursor + 1):
                self._push_history(self.frames[i])
            self.slider.blockSignals(True)
            self.slider.setValue(self.cursor)
            self.slider.blockSignals(False)
            self._update_all(self.frames[self.cursor])

    def _step_forward(self):
        if not self.frames:
            return
        self._pause()
        if self.cursor < len(self.frames) - 1:
            self.cursor += 1
            self.slider.blockSignals(True)
            self.slider.setValue(self.cursor)
            self.slider.blockSignals(False)
            self._render_frame(self.cursor)

    def _on_slider(self, value: int):
        """User dragged slider — seek to that frame."""
        if not self.frames:
            return
        self._pause()
        self.cursor = value
        # Rebuild rolling history up to this point
        self._reset_history()
        start = max(0, value - HISTORY + 1)
        for i in range(start, value + 1):
            self._push_history(self.frames[i])
        self._update_all(self.frames[value])

    def _on_speed_change(self, text: str):
        if self._playing:
            self.timer.setInterval(SPEEDS.get(text, 100))

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render_frame(self, idx: int):
        """Push frame into history then redraw everything."""
        frame = self.frames[idx]
        self._push_history(frame)
        self._update_all(frame)

    def _update_all(self, frame: dict):
        self._update_charts()
        self._update_cards(frame)
        self._update_bars(frame)
        self._update_frame_label()

    def _update_charts(self):
        t = np.array(self.t)
        self.curve_move_d.setData(t, np.array(self.move_dist_h))
        self.curve_stat_d.setData(t, np.array(self.static_dist_h))
        self.curve_det_d.setData(t,  np.array(self.detect_dist_h))
        self.curve_move_e.setData(t, np.array(self.move_energy_h))
        self.curve_stat_e.setData(t, np.array(self.stat_energy_h))

    def _update_cards(self, frame: dict):
        self.card_target[1].setText(str(frame.get("target",          "—")))
        self.card_move_d[1].setText(str(int(frame.get("move_dist",   0))))
        self.card_move_e[1].setText(str(int(frame.get("move_energy", 0))))
        self.card_stat_d[1].setText(str(int(frame.get("static_dist", 0))))
        self.card_stat_e[1].setText(str(int(frame.get("static_energy",0))))
        self.card_detect[1].setText(str(int(frame.get("detect_distance",0))))

    def _update_bars(self, frame: dict):
        mg = [frame.get(f"move_gate_{i}_energy",        0) for i in range(9)]
        sg = [frame.get(f"stationary_gate_{i}_energy",  0) for i in range(9)]
        self.move_bars.setOpts(height=mg)
        self.stat_bars.setOpts(height=sg)

    def _update_frame_label(self):
        self.frame_lbl.setText(f"Frame {self.cursor + 1} / {len(self.frames)}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _set_controls_enabled(self, enabled: bool):
        for w in (self.prev_btn, self.play_btn, self.next_btn,
                  self.slider, self.speed_box):
            w.setEnabled(enabled)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = RadarWindow()
    win.show()

    # Optional: drag-and-drop / CLI argument
    if len(sys.argv) > 1:
        win._load(sys.argv[1])

    sys.exit(app.exec())
