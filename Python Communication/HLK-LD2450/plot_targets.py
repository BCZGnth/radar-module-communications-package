"""
LD2450 Live Serial Plotter
──────────────────────────
Add your frame-parsing line where marked with  >>>  below.

Each parsed frame must be a 12-element list/tuple:
  [x1, y1, speed1, res1,  x2, y2, speed2, res2,  x3, y3, speed3, res3]

Serial reading runs in a background thread — the GUI never blocks.
Parsed frames are posted to the main thread via Qt signals.
"""

import sys
import math
import serial
import serial.tools.list_ports
from collections import deque
from threading import Thread, Event
import serial_protocol as sp
import os

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QComboBox, QStatusBar, QCheckBox,
    QSpinBox,
)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QObject
import pyqtgraph as pg
from pyqtgraph import ArrowItem
import numpy as np

# ── Theme ─────────────────────────────────────────────────────────────────────
pg.setConfigOption("background", "#0a0e1a")
pg.setConfigOption("foreground", "#c8d6e5")

TARGET_COLORS = ["#00f5d4", "#f5a623", "#e84393"]
TRAIL_LEN     = 40
VECTOR_SCALE  = 8.0
PANEL_BG      = "#111827"
BORDER_CLR    = "#1e2d3d"

BTN_STYLE = (
    "QPushButton {"
    f"  background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
    "  border-radius:4px; padding:4px 12px; font-family:'Courier New'; font-size:12px;"
    "}"
    "QPushButton:hover    { background:#1e2d3d; }"
    "QPushButton:pressed  { background:#0a0e1a; }"
    "QPushButton:disabled { color:#3a4a5a; }"
)
COMBO_STYLE = (
    f"background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
    "border-radius:4px; padding:2px 6px; font-family:'Courier New'; font-size:12px;"
)
BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "256000", "460800"]


# ── Serial worker (background thread) ────────────────────────────────────────
class SerialSignals(QObject):
    frame_ready = pyqtSignal(list)   # emits a 12-element list
    error       = pyqtSignal(str)
    connected   = pyqtSignal(str)    # port name
    disconnected= pyqtSignal()


class SerialWorker:
    """
    Runs serial reading in a daemon thread.
    Call start() / stop() from the GUI thread.
    """
    def __init__(self):
        self.signals  = SerialSignals()
        self._stop    = Event()
        self._thread  = None
        self._ser     = None

    def start(self, port: str, baud: int):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, args=(port, baud), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    def _run(self, port: str, baud: int):
        try:
            self._ser = serial.Serial(port, baud, timeout=1)
            self.signals.connected.emit(port)
        except serial.SerialException as e:
            self.signals.error.emit(f"Could not open {port}: {e}")
            return

        try:
            while not self._stop.is_set():
                try:
                    # ───────────────────────────────────────────────────────
                    # >>> ADD YOUR FRAME-READING LINE HERE
                    #
                    # Read enough bytes / lines for one complete LD2450 frame,
                    # parse it, and assign the result to `raw_frame`.
                    #
                    # `raw_frame` must be a 12-element list:
                    #   [x1, y1, speed1, res1,
                    #    x2, y2, speed2, res2,
                    #    x3, y3, speed3, res3]
                    #
                    # Example (replace with your actual parsing logic):
                    #   line = self._ser.readline().decode("utf-8", errors="ignore").strip()
                    #   raw_frame = your_parse_function(line)
                    #
                    # ───────────────────────────────────────────────────────
                    serial_port_line = self._ser.read_until(sp.REPORT_TAIL)

                    raw_frame = sp.read_radar_data(serial_port_line)
    
                    if raw_frame is None:
                        continue   # skip until you have a valid frame

                    if (isinstance(raw_frame, (list, tuple)) and len(raw_frame) == 12
                            and all(isinstance(v, (int, float)) for v in raw_frame)):
                        self.signals.frame_ready.emit(list(raw_frame))
                    else:
                        self.signals.error.emit(
                            f"Bad frame shape: expected 12 numbers, got {raw_frame!r}"
                        )

                except serial.SerialException as e:
                    self.signals.error.emit(f"Serial read error: {e}")
                    break
                except Exception as e:
                    # Don't crash the thread on a single bad parse
                    self.signals.error.emit(f"Parse error: {e}")
                    continue
        finally:
            try:
                self._ser.close()
            except Exception:
                pass
            self.signals.disconnected.emit()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _target_active(x, y, res) -> bool:
    return not (x == 0.0 and y == 0.0 and res == 0.0)


def _list_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


# ── Main Window ───────────────────────────────────────────────────────────────
class LiveRadarWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LD2450 Live Monitor")
        self.setMinimumSize(950, 760)
        self.setStyleSheet("background-color:#0a0e1a; color:#c8d6e5;")

        self._worker      = SerialWorker()
        self._connected   = False
        self._prev_frame  = None
        self.trails       = [deque(maxlen=TRAIL_LEN) for _ in range(3)]
        self._frame_count = 0
        self.test_frames  = []

        # FPS tracking
        self._fps_buf = deque(maxlen=30)
        self._last_t  = None

        self._build_ui()
        self._wire_worker()

        # Refresh ports list every 3 s
        self._port_timer = QTimer()
        self._port_timer.timeout.connect(self._refresh_ports)
        self._port_timer.start(3000)
        self._refresh_ports()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(12, 12, 12, 8)
        main.setSpacing(8)

        # ── Connection bar ────────────────────────────────────────────────────
        conn = QHBoxLayout()
        conn.setSpacing(8)

        title = QLabel("LD2450  //  LIVE RADAR")
        title.setStyleSheet(
            "color:#00f5d4; font-size:15px; font-weight:bold;"
            "font-family:'Courier New'; letter-spacing:3px;"
        )
        conn.addWidget(title)
        conn.addStretch()

        port_lbl = QLabel("Port:")
        port_lbl.setStyleSheet("font-family:'Courier New'; font-size:12px;")
        self.port_box = QComboBox()
        self.port_box.setStyleSheet(COMBO_STYLE)
        self.port_box.setMinimumWidth(140)

        baud_lbl = QLabel("Baud:")
        baud_lbl.setStyleSheet("font-family:'Courier New'; font-size:12px;")
        self.baud_box = QComboBox()
        self.baud_box.setStyleSheet(COMBO_STYLE)
        self.baud_box.addItems(BAUD_RATES)
        self.baud_box.setCurrentText("256000")

        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setStyleSheet(BTN_STYLE)
        self.refresh_btn.setFixedWidth(34)
        self.refresh_btn.setToolTip("Refresh port list")
        self.refresh_btn.clicked.connect(self._refresh_ports)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setStyleSheet(BTN_STYLE)
        self.connect_btn.clicked.connect(self._toggle_connection)

        self.indicator = QLabel("●")
        self.indicator.setStyleSheet("color:#3a4a5a; font-size:18px;")
        self.indicator.setToolTip("Connection status")

        conn.addWidget(port_lbl)
        conn.addWidget(self.port_box)
        conn.addWidget(baud_lbl)
        conn.addWidget(self.baud_box)
        conn.addWidget(self.refresh_btn)
        conn.addWidget(self.connect_btn)
        conn.addWidget(self.indicator)
        main.addLayout(conn)

        # ── 2D plot ───────────────────────────────────────────────────────────
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "X (mm)")
        self.plot.setLabel("left",   "Y (mm)")
        self.plot.setStyleSheet(f"border:1px solid {BORDER_CLR}; border-radius:6px;")
        self.plot.getPlotItem().titleLabel.setText(
            '<span style="color:#6b7fa3;font-size:10pt;font-family:Courier New">'
            'Live Target Positions</span>'
        )
        # Radar origin
        self.plot.plot([0], [0], symbol="+", symbolSize=16,
                       symbolPen=pg.mkPen("#ffffff", width=2), pen=None)

        # Default view range (mm) — adjusts automatically as targets appear
        self.plot.setXRange(-1500, 1500, padding=0)
        self.plot.setYRange(0, -3000,    padding=0)

        # flip the y axis to make the graph more readable.
        self.plot_item = self.plot.getPlotItem()
        # self.plot_item.invertY(True)

        self.trail_curves = []
        self.dot_items    = []
        self.arrow_items  = []

        for i, color in enumerate(TARGET_COLORS):
            trail = self.plot.plot(
                [], [], pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DotLine)
            )
            self.trail_curves.append(trail)

            dot = self.plot.plot(
                [], [], symbol="o", symbolSize=12,
                symbolBrush=pg.mkBrush(color),
                symbolPen=pg.mkPen("#0a0e1a", width=1),
                pen=None,
            )
            self.dot_items.append(dot)

            arrow = ArrowItem(
                angle=0, tipAngle=30, baseAngle=20,
                headLen=16, tailLen=24, tailWidth=3,
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
        opts.setSpacing(20)

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

        trail_lbl = QLabel("Trail length:")
        trail_lbl.setStyleSheet("font-family:'Courier New'; font-size:12px;")
        self.trail_spin = QSpinBox()
        self.trail_spin.setRange(2, 200)
        self.trail_spin.setValue(TRAIL_LEN)
        self.trail_spin.setStyleSheet(
            f"background:{PANEL_BG}; color:#c8d6e5; border:1px solid {BORDER_CLR};"
            "border-radius:4px; padding:2px 4px; font-family:'Courier New'; font-size:12px;"
        )
        self.trail_spin.valueChanged.connect(self._on_trail_len_change)

        self.autorange_cb = QCheckBox("Auto-range")
        self.autorange_cb.setChecked(False)
        self.autorange_cb.setStyleSheet(
            "color:#c8d6e5; font-family:'Courier New'; font-size:12px;"
        )

        self.clear_btn = QPushButton("Clear Trails")
        self.clear_btn.setStyleSheet(BTN_STYLE)
        self.clear_btn.clicked.connect(self._clear_trails)

        self.fps_lbl = QLabel("— fps")
        self.fps_lbl.setStyleSheet(
            "font-family:'Courier New'; font-size:12px; color:#6b7fa3;"
        )

        self.frames_lbl = QLabel("Frames: 0")
        self.frames_lbl.setStyleSheet(
            "font-family:'Courier New'; font-size:12px; color:#6b7fa3;"
        )

        opts.addWidget(self.show_vectors_cb)
        opts.addWidget(self.show_trail_cb)
        opts.addWidget(trail_lbl)
        opts.addWidget(self.trail_spin)
        opts.addWidget(self.autorange_cb)
        opts.addWidget(self.clear_btn)
        opts.addStretch()
        opts.addWidget(self.fps_lbl)
        opts.addWidget(self.frames_lbl)
        main.addLayout(opts)

        # Status bar
        self.status = QStatusBar()
        self.status.setStyleSheet(
            "font-family:'Courier New'; font-size:11px; color:#6b7fa3;"
            f"background:{PANEL_BG}; border-top:1px solid {BORDER_CLR};"
        )
        self.setStatusBar(self.status)
        self.status.showMessage("Select a port and click Connect.")

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
        for key, display in [("x",    "X (mm)"),
                              ("y",    "Y (mm)"),
                              ("speed","Speed (cm/s)"),
                              ("res",  "Dist Res (mm)"),
                              ("vel",  "Δ Pos (mm/f)")]:
            row = QHBoxLayout()
            k = QLabel(f"{display}:")
            k.setStyleSheet("color:#6b7fa3; font-size:10px; font-family:'Courier New';")
            v = QLabel("—")
            v.setStyleSheet("color:#c8d6e5; font-size:10px; font-family:'Courier New';")
            row.addWidget(k)
            row.addStretch()
            row.addWidget(v)
            vbox.addLayout(row)
            labels[key] = v

        return frame, labels

    # ── Worker wiring ─────────────────────────────────────────────────────────
    def _wire_worker(self):
        sig = self._worker.signals
        sig.frame_ready .connect(self._on_frame)
        sig.error       .connect(self._on_error)
        sig.connected   .connect(self._on_connected)
        sig.disconnected.connect(self._on_disconnected)

    # ── Connection ────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        current = self.port_box.currentText()
        ports   = _list_ports()
        self.port_box.blockSignals(True)
        self.port_box.clear()
        if ports:
            self.port_box.addItems(ports)
            if current in ports:
                self.port_box.setCurrentText(current)
        else:
            self.port_box.addItem("— no ports —")
        self.port_box.blockSignals(False)

    def _toggle_connection(self):
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_box.currentText()
        if not port or port.startswith("—"):
            self.status.showMessage("⚠  No valid port selected.")
            return
        try:
            baud = int(self.baud_box.currentText())
        except ValueError:
            self.status.showMessage("⚠  Invalid baud rate.")
            return

        self.connect_btn.setEnabled(False)
        self.status.showMessage(f"Connecting to {port} @ {baud}…")
        self._worker.start(port, baud)
        self.test_frames = []

    def _disconnect(self):
        self._worker.stop()
        self.save_test()
        # _on_disconnected will fire via signal

    def _on_connected(self, port: str):
        self._connected = True
        self.connect_btn.setText("Disconnect")
        self.connect_btn.setEnabled(True)
        self.indicator.setStyleSheet("color:#00f5d4; font-size:18px;")
        self.status.showMessage(f"Connected — {port} @ {self.baud_box.currentText()}")
        self._frame_count = 0

    def _on_disconnected(self):
        self._connected = False
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.indicator.setStyleSheet("color:#3a4a5a; font-size:18px;")
        self.status.showMessage("Disconnected.")

    def _on_error(self, msg: str):
        self.status.showMessage(f"⚠  {msg}")
        # If we were never connected, re-enable the button
        if not self._connected:
            self.connect_btn.setEnabled(True)

    # ── Frame handler (main thread via Qt signal) ──────────────────────────────
    def _on_frame(self, raw: list):
        
        self.test_frames.append(raw)

        import time
        now = time.monotonic()
        if self._last_t is not None:
            self._fps_buf.append(1.0 / max(now - self._last_t, 1e-6))
            if len(self._fps_buf) >= 5:
                self.fps_lbl.setText(f"{sum(self._fps_buf)/len(self._fps_buf):.1f} fps")
        self._last_t = now

        self._frame_count += 1
        self.frames_lbl.setText(f"Frames: {self._frame_count}")

        # Unpack 3 targets
        targets = []
        for t in range(3):
            b = t * 4
            targets.append({
                "x":     float(raw[b]),
                "y":     float(raw[b + 1]),
                "speed": float(raw[b + 2]),
                "res":   float(raw[b + 3]),
            })

        show_vec   = self.show_vectors_cb.isChecked()
        show_trail = self.show_trail_cb.isChecked()

        for t_idx, tgt in enumerate(targets):
            x, y, res = tgt["x"], tgt["y"], tgt["res"]
            active = _target_active(x, y, res)

            # Trail
            if active:
                self.trails[t_idx].append((x, y))
            else:
                self.trails[t_idx].clear()

            trail_pts = list(self.trails[t_idx])
            if active and show_trail and len(trail_pts) > 1:
                self.trail_curves[t_idx].setData(
                    [p[0] for p in trail_pts],
                    [p[1] for p in trail_pts],
                )
                self.trail_curves[t_idx].setVisible(True)
            else:
                self.trail_curves[t_idx].setData([], [])

            # Dot
            if active:
                self.dot_items[t_idx].setData([x], [y])
            else:
                self.dot_items[t_idx].setData([], [])

            # Velocity vector
            dx = dy = 0.0
            arrow = self.arrow_items[t_idx]
            if active and show_vec and self._prev_frame is not None:
                prev = self._prev_frame[t_idx]
                if _target_active(prev["x"], prev["y"], prev["res"]):
                    dx = (x - prev["x"]) * VECTOR_SCALE
                    dy = (y - prev["y"]) * VECTOR_SCALE

            if active and show_vec and (dx != 0.0 or dy != 0.0):
                angle_deg = math.degrees(math.atan2(dy, dx))
                arrow.setStyle(angle=180 - angle_deg)
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
                cards["res"].setText(f"{res:.0f}")
                cards["vel"].setText(f"{mag:.1f}" if self._prev_frame else "—")
            else:
                for v in cards.values():
                    v.setText("—")

        # Auto-range
        if self.autorange_cb.isChecked():
            active_pts = [
                (t["x"], t["y"]) for t in targets
                if _target_active(t["x"], t["y"], t["res"])
            ]
            if active_pts:
                self.plot.enableAutoRange()

        self._prev_frame = targets

    # ── Option handlers ───────────────────────────────────────────────────────
    def _on_trail_toggle(self, state):
        if not state:
            for curve in self.trail_curves:
                curve.setData([], [])

    def _on_trail_len_change(self, value: int):
        for trail in self.trails:
            trail = deque(trail, maxlen=value)   # resize
        # Re-create with new maxlen
        self.trails = [deque(t, maxlen=value) for t in self.trails]

    def _clear_trails(self):
        for trail in self.trails:
            trail.clear()
        for curve in self.trail_curves:
            curve.setData([], [])

    def closeEvent(self, event):
        self._worker.stop()
        super().closeEvent(event)

    def save_test(self):
        FOLDER = "tests"
        PREFIX = "ld2450_test_"
        EXTENSION = ".json"

        os.makedirs(FOLDER, exist_ok=True)

        existing = os.listdir(FOLDER)
        numbers = []
        for name in existing:
            if name.startswith(PREFIX) and name.endswith(EXTENSION):
                middle = name[len(PREFIX):-len(EXTENSION)]
                if middle.isdigit():
                    numbers.append(int(middle))

        next_number = max(numbers, default=0) + 1
        filename = f"{PREFIX}{next_number:03d}{EXTENSION}"
        filepath = os.path.join(FOLDER, filename)
            
        with open(filepath, "w") as f:
            import json
            json.dump(self.test_frames, f)

        self.test_frames = []

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = LiveRadarWindow()
    win.show()
    sys.exit(app.exec())