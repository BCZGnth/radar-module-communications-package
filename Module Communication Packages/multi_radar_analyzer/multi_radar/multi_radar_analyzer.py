"""
Multi-Radar Analyzer — Python / PyQt6 + pyqtgraph
===================================================
Supports:
  • RD-03E      (Ai-Thinker)   — 1D distance
  • HLK-LD2450  (Hi-Link)      — 2D multi-target XY
  • HLK-LD2410C (Hi-Link)      — 1D distance + presence zones

Manual radar selection; tabbed Distance / 2D-Spatial view for 2D radars.
Test saves as:  <RadarName>_YYYYMMDD_HHMMSS.csv

Requirements:
    pip install PyQt6 pyqtgraph pyserial
"""

from __future__ import annotations
import sys, csv, time, math
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import serial
import serial.tools.list_ports

from PyQt6.QtCore  import (Qt, QThread, pyqtSignal, QMutex, QMutexLocker,
                            QTimer)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QTextEdit, QSplitter, QFileDialog, QMessageBox, QToolBar,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtGui import QPalette, QColor, QFont

import pyqtgraph as pg
import numpy as np


# ══════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ══════════════════════════════════════════════════════════════
@dataclass
class Target:
    """One tracked target from any radar."""
    x_mm:   float = 0.0   # positive = right of sensor
    y_mm:   float = 0.0   # positive = in front of sensor
    speed:  float = 0.0   # cm/s (LD2450 only)

    @property
    def distance_m(self) -> float:
        """Straight-line distance in metres (Y component for 1D radars)."""
        return abs(self.y_mm) / 1000.0

    @property
    def y_m(self) -> float:
        return self.y_mm / 1000.0

    @property
    def x_m(self) -> float:
        return self.x_mm / 1000.0


@dataclass
class RadarFrame:
    elapsed_s:   float
    targets:     list[Target] = field(default_factory=list)
    status:      int   = 0     # RD-03E/LD2410C: 0=none 1=static/moving_dist 2=moving 3=combined
    move_dist_m: float = 0.0   # LD2410C moving distance gate
    stat_dist_m: float = 0.0   # LD2410C static distance gate
    # Energy gates (LD2410C engineering mode, 9 gates each)
    move_energy: list[int] = field(default_factory=list)
    stat_energy: list[int] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
#  RADAR DEFINITIONS
# ══════════════════════════════════════════════════════════════
RADAR_TYPES = {
    "RD-03E":     {"baud": 256000, "is_2d": False,  "color": "#00d4ff"},
    "HLK-LD2450": {"baud": 256000, "is_2d": True,   "color": "#2ecc71"},
    "HLK-LD2410C":{"baud": 256000, "is_2d": False,  "color": "#f39c12"},
}

LD2410C_STATUS = {0:"None", 1:"Moving", 2:"Static", 3:"Moving+Static"}
RD03E_STATUS   = {0:"None", 1:"Static", 2:"Moving"}

TARGET_COLORS = ["#00d4ff","#ff6b35","#a855f7"]  # up to 3 LD2450 targets


# ══════════════════════════════════════════════════════════════
#  PARSERS (pure functions, no I/O)
# ══════════════════════════════════════════════════════════════
def _parse_rd03e(buf: bytearray, t0: float) -> tuple[list[RadarFrame], bytearray]:
    frames = []
    while len(buf) >= 7:
        idx = next((i for i in range(len(buf)-6)
                    if buf[i]==0xAA and buf[i+1]==0xAA
                    and buf[i+5]==0x55 and buf[i+6]==0x55), -1)
        if idx < 0:
            buf = buf[-(6):]
            break
        buf = buf[idx:]
        status   = buf[2]
        raw_dist = buf[3] | (buf[4] << 8)
        dist_mm  = raw_dist * 10.0   # cm→mm
        tgt = Target(x_mm=0.0, y_mm=dist_mm)
        frames.append(RadarFrame(elapsed_s=time.monotonic()-t0,
                                  targets=[tgt] if raw_dist>0 else [],
                                  status=status))
        buf = buf[7:]
    return frames, buf


def _parse_ld2450(buf: bytearray, t0: float) -> tuple[list[RadarFrame], bytearray]:
    # Frame: AA FF 03 00  [target1 8B][target2 8B][target3 8B]  55 CC  = 30 bytes
    HEADER = bytes([0xAA,0xFF,0x03,0x00])
    FOOTER = bytes([0x55,0xCC])
    frames = []
    while len(buf) >= 30:
        idx = next((i for i in range(len(buf)-29)
                    if buf[i:i+4]==HEADER and buf[i+28:i+30]==FOOTER), -1)
        if idx < 0:
            buf = buf[-29:]
            break
        buf = buf[idx:]
        targets = []
        for t in range(3):
            base = 4 + t*8
            raw_x = int.from_bytes(buf[base:base+2],   'little', signed=True)
            raw_y = int.from_bytes(buf[base+2:base+4], 'little', signed=True)
            raw_s = int.from_bytes(buf[base+4:base+6], 'little', signed=True)
            # LD2450 sign encoding: if negative → real = -(value + 2^15)
            x = raw_x if raw_x >= 0 else -(abs(raw_x) - 0x8000) if (raw_x & 0x8000) else raw_x
            y = raw_y if raw_y >= 0 else -(abs(raw_y) - 0x8000) if (raw_y & 0x8000) else raw_y
            speed = raw_s if raw_s >= 0 else -(abs(raw_s) - 0x8000) if (raw_s & 0x8000) else raw_s
            # Zero target = not present
            if x != 0 or y != 0:
                targets.append(Target(x_mm=float(x), y_mm=float(y), speed=float(speed)))
        frames.append(RadarFrame(elapsed_s=time.monotonic()-t0, targets=targets))
        buf = buf[30:]
    return frames, buf


def _parse_ld2410c(buf: bytearray, t0: float) -> tuple[list[RadarFrame], bytearray]:
    # Reporting frame: FD FC FB FA [len_L len_H] 02 AA [data] 55 00 [checksum] 04 03 02 01
    # Basic data:  type=0x02 0xAA, then 13 bytes basic block 0x55 0x00 check 04 03 02 01
    HEADER = bytes([0xFD,0xFC,0xFB,0xFA])
    FOOTER = bytes([0x04,0x03,0x02,0x01])
    frames = []
    while len(buf) >= 12:
        idx = next((i for i in range(len(buf)-3)
                    if buf[i:i+4]==HEADER), -1)
        if idx < 0:
            buf = buf[-3:]
            break
        buf = buf[idx:]
        if len(buf) < 6:
            break
        data_len = int.from_bytes(buf[4:6], 'little')
        total    = 4 + 2 + data_len + 4   # header+len_field+data+footer
        if len(buf) < total:
            break
        frame_bytes = buf[:total]
        buf = buf[total:]
        # Verify footer
        if frame_bytes[-4:] != FOOTER:
            continue
        payload = frame_bytes[6:6+data_len]
        # payload[0] = 0x02 (report type), payload[1] = 0xAA (data head)
        if len(payload) < 13 or payload[0] != 0x02 or payload[1] != 0xAA:
            continue
        detection_type  = payload[2]
        move_dist_cm    = int.from_bytes(payload[3:5],  'little')
        move_energy     = payload[5]
        stat_dist_cm    = int.from_bytes(payload[6:8],  'little')
        stat_energy     = payload[8]
        detect_dist_cm  = int.from_bytes(payload[9:11], 'little')
        # pick representative distance: prefer detect_dist, fallback to max of move/stat
        if detect_dist_cm > 0:
            dist_mm = detect_dist_cm * 10.0
        elif move_dist_cm > 0:
            dist_mm = move_dist_cm * 10.0
        elif stat_dist_cm > 0:
            dist_mm = stat_dist_cm * 10.0
        else:
            dist_mm = 0.0

        # Engineering mode energy gates (optional, 10 bytes each)
        move_eng, stat_eng = [], []
        if len(payload) >= 35 and payload[11] == 0x01:  # eng marker
            move_eng = list(payload[12:21])
            stat_eng = list(payload[21:30])

        tgt = Target(x_mm=0.0, y_mm=dist_mm)
        frames.append(RadarFrame(
            elapsed_s   = time.monotonic()-t0,
            targets     = [tgt] if dist_mm>0 else [],
            status      = detection_type,
            move_dist_m = move_dist_cm/100.0,
            stat_dist_m = stat_dist_cm/100.0,
            move_energy = move_eng,
            stat_energy = stat_eng,
        ))
    return frames, buf


PARSERS = {
    "RD-03E":      _parse_rd03e,
    "HLK-LD2450":  _parse_ld2450,
    "HLK-LD2410C": _parse_ld2410c,
}


# ══════════════════════════════════════════════════════════════
#  SERIAL READER THREAD
# ══════════════════════════════════════════════════════════════
class SerialReader(QThread):
    frames_ready  = pyqtSignal(list)   # list[RadarFrame]
    error_occurred = pyqtSignal(str)

    def __init__(self, port: str, baud: int, radar_type: str, parent=None):
        super().__init__(parent)
        self._port       = port
        self._baud       = baud
        self._radar_type = radar_type
        self._running    = False
        self._mutex      = QMutex()
        self._t0: float  = 0.0

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False

    def run(self):
        self._running = True
        buf = bytearray()
        parser = PARSERS[self._radar_type]
        try:
            with serial.Serial(self._port, self._baud,
                               bytesize=8, parity='N', stopbits=1,
                               timeout=0.05) as ser:
                self._t0 = time.monotonic()
                while True:
                    with QMutexLocker(self._mutex):
                        if not self._running:
                            break
                    chunk = ser.read(128)
                    if chunk:
                        buf.extend(chunk)
                        frames, buf = parser(buf, self._t0)
                        if frames:
                            self.frames_ready.emit(frames)
        except serial.SerialException as e:
            self.error_occurred.emit(str(e))


# ══════════════════════════════════════════════════════════════
#  2D SPATIAL WIDGET  (LD2450 top-down view)
# ══════════════════════════════════════════════════════════════
class SpatialWidget(QWidget):
    MAX_TRAIL = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)

        self.plot = pg.PlotWidget(title="Top-Down Spatial View  (X = lateral, Y = depth)")
        self.plot.setLabel('left',   'Depth Y', units='m')
        self.plot.setLabel('bottom', 'Lateral X', units='m')
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setXRange(-4, 4)
        self.plot.setYRange(-0.5, 6)
        self.plot.setAspectLocked(True)
        self.plot.addLegend()

        # Sensor origin marker
        origin = pg.ScatterPlotItem(
            [0], [0], symbol='t', size=14,
            brush=pg.mkBrush('#ffffff'), pen=pg.mkPen(None))
        self.plot.addItem(origin)
        # Field-of-view cone (±60° typical LD2450)
        for angle in [-60, 60]:
            r = 6.0
            ax = r * math.sin(math.radians(angle))
            ay = r * math.cos(math.radians(angle))
            line = pg.PlotDataItem([0, ax],[0, ay],
                pen=pg.mkPen(color='#444466', width=1, style=Qt.PenStyle.DashLine))
            self.plot.addItem(line)

        # Per-target trails + current position
        self._trails: list[deque] = [deque(maxlen=self.MAX_TRAIL) for _ in range(3)]
        self._trail_curves = []
        self._dots = []
        self._labels = []
        for i in range(3):
            col = TARGET_COLORS[i]
            tc = self.plot.plot([], [], pen=pg.mkPen(col, width=1), alpha=0.5,
                                name=f"Target {i+1}")
            dot = pg.ScatterPlotItem([], [], symbol='o', size=14,
                                     brush=pg.mkBrush(col),
                                     pen=pg.mkPen('#ffffff', width=1))
            self.plot.addItem(dot)
            self._trail_curves.append(tc)
            self._dots.append(dot)

        layout.addWidget(self.plot)

        # Live text readout below chart
        self.readout = QLabel("No targets")
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.readout.setStyleSheet("QLabel{color:#aaa;font-size:11px;padding:4px;}")
        layout.addWidget(self.readout)

    def update_targets(self, targets: list[Target]):
        texts = []
        for i in range(3):
            if i < len(targets):
                t = targets[i]
                self._trails[i].append((t.x_m, t.y_m))
                xs = [p[0] for p in self._trails[i]]
                ys = [p[1] for p in self._trails[i]]
                self._trail_curves[i].setData(xs, ys)
                self._dots[i].setData([t.x_m], [t.y_m])
                d = math.sqrt(t.x_m**2 + t.y_m**2)
                texts.append(f"T{i+1}: X={t.x_m:+.2f}m  Y={t.y_m:.2f}m  dist={d:.2f}m  spd={t.speed:.0f}cm/s")
            else:
                self._trail_curves[i].setData([], [])
                self._dots[i].setData([], [])
        self.readout.setText("   |   ".join(texts) if texts else "No targets")

    def clear(self):
        for trail in self._trails:
            trail.clear()
        for c in self._trail_curves: c.setData([], [])
        for d in self._dots:         d.setData([], [])
        self.readout.setText("No targets")


# ══════════════════════════════════════════════════════════════
#  LD2410C ENERGY BAR WIDGET
# ══════════════════════════════════════════════════════════════
class EnergyBarWidget(QWidget):
    """Gate energy bars for LD2410C engineering mode."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)

        self.move_plot = pg.PlotWidget(title="Moving Gate Energy (gates 0–8)")
        self.stat_plot = pg.PlotWidget(title="Static Gate Energy (gates 0–8)")
        for pw in (self.move_plot, self.stat_plot):
            pw.setYRange(0, 100)
            pw.setXRange(-0.5, 8.5)
            pw.setMaximumHeight(160)
            pw.showGrid(y=True, alpha=0.3)
            pw.setLabel('bottom','Gate')
            pw.setLabel('left','Energy')

        gates = list(range(9))
        self._move_bars = pg.BarGraphItem(x=gates, height=[0]*9, width=0.6,
                                           brush='#ff6b35')
        self._stat_bars = pg.BarGraphItem(x=gates, height=[0]*9, width=0.6,
                                           brush='#2ecc71')
        self.move_plot.addItem(self._move_bars)
        self.stat_plot.addItem(self._stat_bars)

        layout.addWidget(self.move_plot)
        layout.addWidget(self.stat_plot)

    def update_energy(self, move: list[int], stat: list[int]):
        if len(move) == 9:
            self._move_bars.setOpts(height=move)
        if len(stat) == 9:
            self._stat_bars.setOpts(height=stat)

    def clear(self):
        self._move_bars.setOpts(height=[0]*9)
        self._stat_bars.setOpts(height=[0]*9)


# ══════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Multi-Radar Analyzer")
        self.setMinimumSize(1150, 780)

        self._reader: Optional[SerialReader] = None
        self._radar_type = "RD-03E"
        self._data: list[dict] = []    # raw rows for CSV
        self._frame_count = 0
        self._t0_wall: Optional[datetime] = None  # wall-clock at first frame

        # Rolling distance buffer (Y component, may be negative for LD2450)
        self._times  = deque(maxlen=20000)
        self._dists  = deque(maxlen=20000)   # |y| in metres, sign preserved

        self._build_ui()
        self._build_toolbar()
        self._refresh_ports()
        self._on_radar_type_changed(self._radar_type)
        self._set_connected(False)

    # ── UI ───────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8,8,8,8)
        layout.setSpacing(6)

        # ── Top controls row ──
        top = QHBoxLayout()

        # Port group
        pg_box = QGroupBox("Serial Port")
        pg_lay = QHBoxLayout(pg_box)
        self.port_combo  = QComboBox(); self.port_combo.setMinimumWidth(190)
        self.baud_combo  = QComboBox()
        self.baud_combo.addItems(["256000","115200","57600","38400","19200","9600"])
        self.baud_combo.setCurrentText("256000")
        self.refresh_btn    = QPushButton("⟳"); self.refresh_btn.setFixedWidth(30)
        self.connect_btn    = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.connect_btn.setStyleSheet(
            "QPushButton{background:#27ae60;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 12px;}QPushButton:hover{background:#2ecc71;}")
        self.disconnect_btn.setStyleSheet(
            "QPushButton{background:#c0392b;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 12px;}QPushButton:hover{background:#e74c3c;}")
        for w in (QLabel("Port:"),self.port_combo,self.refresh_btn,
                  QLabel("Baud:"),self.baud_combo,
                  self.connect_btn,self.disconnect_btn):
            pg_lay.addWidget(w)

        # Radar type group
        rt_box = QGroupBox("Radar Type")
        rt_lay = QHBoxLayout(rt_box)
        self.radar_combo = QComboBox()
        self.radar_combo.addItems(list(RADAR_TYPES.keys()))
        rt_lay.addWidget(self.radar_combo)

        # Chart settings
        cs_box = QGroupBox("Chart Settings")
        cs_lay = QHBoxLayout(cs_box)
        self.window_spin = QSpinBox()
        self.window_spin.setRange(5,600); self.window_spin.setValue(30)
        self.window_spin.setSuffix(" s")
        self.maxdist_spin = QDoubleSpinBox()
        self.maxdist_spin.setRange(0.1,10.0); self.maxdist_spin.setValue(6.0)
        self.maxdist_spin.setSingleStep(0.5); self.maxdist_spin.setSuffix(" m")
        apply_btn = QPushButton("Apply")
        for w in (QLabel("Window:"),self.window_spin,
                  QLabel("Max:"),self.maxdist_spin,apply_btn):
            cs_lay.addWidget(w)

        # Live readout
        ro_box = QGroupBox("Live Readout")
        ro_lay = QHBoxLayout(ro_box)
        self.status_dot  = QLabel("●"); self.status_dot.setFixedWidth(22)
        self.dist_label  = QLabel("-- m")
        self.pres_label  = QLabel("None")
        self.frame_label = QLabel("Frames: 0")
        big = QFont(); big.setPointSize(13); big.setBold(True)
        self.dist_label.setFont(big)
        for w in (self.status_dot, QLabel("Dist:"), self.dist_label,
                  QLabel("Status:"), self.pres_label, self.frame_label):
            ro_lay.addWidget(w)

        top.addWidget(pg_box, 3)
        top.addWidget(rt_box, 1)
        top.addWidget(cs_box, 2)
        top.addWidget(ro_box, 2)
        layout.addLayout(top)

        # ── Tab widget (Distance | 2D Spatial) ──
        self.tabs = QTabWidget()

        # ── Tab 1: Distance plot + table + log ──
        dist_tab = QWidget()
        dist_layout = QVBoxLayout(dist_tab)
        dist_layout.setContentsMargins(0,4,0,0)

        vsplit = QSplitter(Qt.Orientation.Vertical)

        # Distance chart
        pg.setConfigOption('background','#1a1a22')
        pg.setConfigOption('foreground','#cccccc')
        self.dist_plot = pg.PlotWidget(title="Distance Over Time")
        self.dist_plot.setLabel('left',  'Distance', units='m')
        self.dist_plot.setLabel('bottom','Time', units='s')
        self.dist_plot.showGrid(x=True, y=True, alpha=0.25)
        self.dist_plot.setYRange(-0.5, 6)
        self.dist_plot.setXRange(0, 30)
        self.dist_plot.setMinimumHeight(260)
        # zero line
        zero_line = pg.InfiniteLine(pos=0, angle=0,
            pen=pg.mkPen('#555566', width=1, style=Qt.PenStyle.DashLine))
        self.dist_plot.addItem(zero_line)
        # threshold line
        self.thresh_line = pg.InfiniteLine(
            pos=1.0, angle=0, movable=True,
            pen=pg.mkPen('#e74c3c', width=1, style=Qt.PenStyle.DashLine),
            label='Threshold: {value:.2f} m',
            labelOpts={'color':'#e74c3c','position':0.05})
        self.dist_plot.addItem(self.thresh_line)
        # up to 3 series (for LD2450 multi-target distances)
        self._dist_curves = []
        self._dist_bufs: list[tuple[deque,deque]] = []
        for i in range(3):
            col = TARGET_COLORS[i]
            c = self.dist_plot.plot([], [], pen=pg.mkPen(col, width=2),
                                    name=f"T{i+1}" if i>0 else "Distance")
            self._dist_curves.append(c)
            self._dist_bufs.append((deque(maxlen=20000), deque(maxlen=20000)))

        vsplit.addWidget(self.dist_plot)

        # Bottom: table | log
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(
            ["Time (s)","Distance (m)","X (m)","Y (m)","Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(155)
        self.log_view.setFont(QFont("Courier New", 9))
        self.log_view.setPlaceholderText("Event log...")

        hsplit.addWidget(self.table)
        hsplit.addWidget(self.log_view)
        hsplit.setSizes([650,350])
        vsplit.addWidget(hsplit)
        vsplit.setSizes([400,200])
        dist_layout.addWidget(vsplit)
        self.tabs.addTab(dist_tab, "📈  Distance")

        # ── Tab 2: 2D Spatial (shown only for 2D radars) ──
        spatial_tab = QWidget()
        sp_layout = QVBoxLayout(spatial_tab)
        sp_layout.setContentsMargins(0,4,0,0)

        sp_split = QSplitter(Qt.Orientation.Vertical)
        self.spatial_widget = SpatialWidget()
        self.energy_widget  = EnergyBarWidget()
        sp_split.addWidget(self.spatial_widget)
        sp_split.addWidget(self.energy_widget)
        sp_split.setSizes([450,220])
        sp_layout.addWidget(sp_split)
        self.tabs.addTab(spatial_tab, "🗺  2D Spatial / Energy")

        layout.addWidget(self.tabs, 1)

        # ── Connections ──
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.refresh_btn.clicked.connect(self._refresh_ports)
        apply_btn.clicked.connect(self._apply_axes)
        self.radar_combo.currentTextChanged.connect(self._on_radar_type_changed)

    def _build_toolbar(self):
        tb = self.addToolBar("Main"); tb.setMovable(False)
        for label, slot in [("💾  Save Test", self._save_test),
                             ("📂  Open Test", self._open_test),
                             (None, None),
                             ("🗑  Clear",     self._clear_data)]:
            if label is None:
                tb.addSeparator()
            else:
                a = tb.addAction(label)
                a.triggered.connect(slot)

    # ── Helpers ──────────────────────────────────────────────
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_view.append(f"[{ts}] {msg}")

    def _set_connected(self, connected: bool):
        for w in (self.connect_btn, self.port_combo, self.baud_combo,
                  self.refresh_btn, self.radar_combo):
            w.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        if not connected:
            self.status_dot.setStyleSheet("QLabel{color:#555;font-size:18px;}")
            self.pres_label.setText("None")
            self.statusBar().showMessage("Disconnected")

    def _on_radar_type_changed(self, rtype: str):
        self._radar_type = rtype
        is_2d = RADAR_TYPES[rtype]["is_2d"]
        self.tabs.setTabVisible(1, True)  # always show tab 2
        tab2_label = "🗺  2D Spatial" if is_2d else "📊  Energy Gates"
        self.tabs.setTabText(1, tab2_label)
        # Show multi-target curves only for LD2450
        for i in range(1, 3):
            self._dist_curves[i].setVisible(is_2d)
        # Update baud default
        self.baud_combo.setCurrentText(str(RADAR_TYPES[rtype]["baud"]))
        self._log(f"Radar type set to: {rtype}")

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        if not ports:
            self.port_combo.addItem("(none)", "(none)")
        else:
            for p in ports:
                self.port_combo.addItem(
                    f"{p.device} — {p.description or 'Serial Port'}", p.device)
        self._log(f"Found {len(ports)} serial port(s)")

    def _apply_axes(self):
        w = self.window_spin.value()
        md = self.maxdist_spin.value()
        ts = list(self._dist_bufs[0][0])
        if ts:
            xmin = max(0.0, ts[-1]-w)
            self.dist_plot.setXRange(xmin, xmin+w, padding=0)
        else:
            self.dist_plot.setXRange(0, w, padding=0)
        self.dist_plot.setYRange(-md*0.1, md, padding=0)

    def _status_color(self, status: int) -> str:
        if self._radar_type == "RD-03E":
            return {0:"#555", 1:"#f39c12", 2:"#e74c3c"}.get(status,"#555")
        return {0:"#555", 1:"#e74c3c", 2:"#f39c12", 3:"#e74c3c"}.get(status,"#555")

    def _status_label(self, frame: RadarFrame) -> str:
        if self._radar_type == "RD-03E":
            return RD03E_STATUS.get(frame.status, "?")
        if self._radar_type == "HLK-LD2410C":
            return LD2410C_STATUS.get(frame.status, "?")
        return f"{len(frame.targets)} target(s)"

    # ── Serial connect/disconnect ────────────────────────────
    def _on_connect(self):
        port = self.port_combo.currentData()
        if not port or port == "(none)":
            QMessageBox.warning(self,"No Port","Select a valid COM port.")
            return
        baud = int(self.baud_combo.currentText())
        rtype = self.radar_combo.currentText()
        self._reader = SerialReader(port, baud, rtype, self)
        self._reader.frames_ready.connect(self._on_frames)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.start()
        self._radar_type = rtype
        self._t0_wall = None
        self._set_connected(True)
        self.statusBar().showMessage(f"Connected: {port} @ {baud}  [{rtype}]")
        self._log(f"Connected to {port} @ {baud} baud  [{rtype}]")

    def _on_disconnect(self):
        if self._reader:
            self._reader.stop()
            self._reader.wait(2000)
            self._reader = None
        self._set_connected(False)
        self._log("Disconnected")

    def _on_error(self, msg: str):
        self._log(f"ERROR: {msg}")
        self._set_connected(False)
        QMessageBox.critical(self,"Serial Error", msg)

    # ── Frame handler ────────────────────────────────────────
    def _on_frames(self, frames: list[RadarFrame]):
        for f in frames:
            self._frame_count += 1
            if self._t0_wall is None:
                self._t0_wall = datetime.now()
            self._ingest_frame(f)

    def _ingest_frame(self, f: RadarFrame):
        t = f.elapsed_s
        is_2d = RADAR_TYPES[self._radar_type]["is_2d"]

        # ── Distance plot ──
        # For all radars: plot Y component of each target (signed, in metres)
        # This handles negative Y naturally (e.g. LD2450 behind sensor)
        for i in range(3):
            tb, db = self._dist_bufs[i]
            if i < len(f.targets):
                tgt = f.targets[i]
                # Y is depth — preserving sign so negative values show below zero
                dist_val = tgt.y_m
            else:
                dist_val = None
            if dist_val is not None:
                tb.append(t); db.append(dist_val)
                self._dist_curves[i].setData(list(tb), list(db))

        # Primary distance for readout (target 0 y, or first present)
        if f.targets:
            primary_dist = f.targets[0].y_m
        else:
            primary_dist = 0.0

        # Scroll X axis
        w = self.window_spin.value()
        md = self.maxdist_spin.value()
        xmin = max(0.0, t - w)
        self.dist_plot.setXRange(xmin, xmin+w, padding=0)
        # Y range: accommodate negative values from 2D radars
        if is_2d:
            self.dist_plot.setYRange(-md, md, padding=0)
        else:
            self.dist_plot.setYRange(-0.1, md, padding=0)

        # ── Live readout ──
        col = self._status_color(f.status)
        self.status_dot.setStyleSheet(f"QLabel{{color:{col};font-size:20px;}}")
        self.dist_label.setText(f"{abs(primary_dist):.2f} m")
        self.pres_label.setText(self._status_label(f))
        self.frame_label.setText(f"Frames: {self._frame_count}")

        # ── 2D / energy tabs ──
        if is_2d:
            self.spatial_widget.update_targets(f.targets)
        else:
            self.energy_widget.update_energy(f.move_energy, f.stat_energy)

        # ── Table (newest first, cap 2000) ──
        self.table.setSortingEnabled(False)
        self.table.insertRow(0)
        x_m = f.targets[0].x_m if f.targets else 0.0
        y_m = f.targets[0].y_m if f.targets else 0.0
        dist_m = abs(y_m) if not is_2d else math.sqrt(x_m**2 + y_m**2)
        self.table.setItem(0,0,QTableWidgetItem(f"{t:.3f}"))
        self.table.setItem(0,1,QTableWidgetItem(f"{dist_m:.3f}"))
        self.table.setItem(0,2,QTableWidgetItem(f"{x_m:+.3f}"))
        self.table.setItem(0,3,QTableWidgetItem(f"{y_m:+.3f}"))
        self.table.setItem(0,4,QTableWidgetItem(self._status_label(f)))
        self.table.setSortingEnabled(True)
        if self.table.rowCount() > 2000:
            self.table.setRowCount(2000)

        # ── Store for CSV ──
        row = {"time_s": t, "status": self._status_label(f)}
        for i, tgt in enumerate(f.targets):
            row[f"t{i+1}_x_m"]    = round(tgt.x_m, 4)
            row[f"t{i+1}_y_m"]    = round(tgt.y_m, 4)
            row[f"t{i+1}_dist_m"] = round(tgt.distance_m, 4)
            row[f"t{i+1}_speed"]  = round(tgt.speed, 1)
        if self._radar_type == "HLK-LD2410C":
            row["move_dist_m"] = round(f.move_dist_m, 3)
            row["stat_dist_m"] = round(f.stat_dist_m, 3)
        self._data.append(row)

    # ── Save / Open ──────────────────────────────────────────
    def _csv_filename(self) -> str:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = self._radar_type.replace("-","_").replace(" ","_")
        return f"{name}_{ts}.csv"

    def _save_test(self):
        if not self._data:
            QMessageBox.information(self,"Save","No data recorded yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Test", self._csv_filename(),
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            # Collect all keys
            all_keys = list(dict.fromkeys(k for row in self._data for k in row))
            with open(path,"w",newline="") as f:
                w = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
                w.writeheader()
                for row in self._data:
                    w.writerow(row)
            self._log(f"Saved {len(self._data)} rows → {path}")
            QMessageBox.information(self,"Saved",
                f"Saved {len(self._data)} frames to:\n{path}")
        except OSError as e:
            QMessageBox.critical(self,"Error",str(e))

    def _open_test(self):
        path, _ = QFileDialog.getOpenFileName(
            self,"Open Test","","CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            with open(path,newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError as e:
            QMessageBox.critical(self,"Error",str(e)); return

        self._clear_data(confirm=False)

        # Detect radar type from filename
        fname = path.replace("\\","/").split("/")[-1].lower()
        for rt in RADAR_TYPES:
            if rt.lower().replace("-","_") in fname.replace("-","_"):
                self._radar_type = rt
                self.radar_combo.setCurrentText(rt)
                self._on_radar_type_changed(rt)
                break

        is_2d = RADAR_TYPES[self._radar_type]["is_2d"]

        for row in rows:
            try:
                t = float(row.get("time_s",0))
            except ValueError:
                continue
            # Reconstruct frame for display
            self._dist_bufs[0][0].append(t)
            if "t1_y_m" in row:
                y = float(row.get("t1_y_m",0))
                x = float(row.get("t1_x_m",0))
                self._dist_bufs[0][1].append(y)
                if is_2d:
                    for i in range(1,3):
                        key = f"t{i+1}_y_m"
                        if key in row and row[key]:
                            self._dist_bufs[i][0].append(t)
                            self._dist_bufs[i][1].append(float(row[key]))
            else:
                # fallback column name
                d = float(row.get("distance_m", row.get("t1_dist_m", 0)))
                self._dist_bufs[0][1].append(d)

            self._data.append(row)
            self._frame_count += 1

        # Redraw
        for i in range(3):
            tb, db = self._dist_bufs[i]
            if tb:
                self._dist_curves[i].setData(list(tb), list(db))

        # Fit axes
        all_t = list(self._dist_bufs[0][0])
        all_d = list(self._dist_bufs[0][1])
        if all_t:
            self.dist_plot.setXRange(0, max(all_t), padding=0.02)
        if all_d:
            mn, mx = min(all_d), max(all_d)
            pad = max(abs(mn), abs(mx)) * 0.1 or 0.5
            self.dist_plot.setYRange(mn-pad, mx+pad, padding=0)

        self.frame_label.setText(f"Frames: {self._frame_count}")
        import os
        self.statusBar().showMessage(
            f"Viewing: {os.path.basename(path)}  [{self._radar_type}]  ({len(rows)} frames)")
        self._log(f"Loaded {len(rows)} rows — detected radar: {self._radar_type}")
        QMessageBox.information(self,"Opened",
            f"Loaded {len(rows)} frames\nRadar: {self._radar_type}\n{path}")

    def _clear_data(self, confirm=True):
        if confirm:
            if QMessageBox.question(self,"Clear","Clear all data?",
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No
                    ) != QMessageBox.StandardButton.Yes:
                return
        self._data.clear()
        self._frame_count = 0
        self._t0_wall = None
        for i in range(3):
            self._dist_bufs[i][0].clear()
            self._dist_bufs[i][1].clear()
            self._dist_curves[i].setData([],[])
        self.table.setRowCount(0)
        self.spatial_widget.clear()
        self.energy_widget.clear()
        self.dist_label.setText("-- m")
        self.frame_label.setText("Frames: 0")
        self._log("Data cleared")

    def closeEvent(self, event):
        if self._reader:
            self._reader.stop()
            self._reader.wait(2000)
        event.accept()


# ══════════════════════════════════════════════════════════════
#  DARK PALETTE + ENTRY POINT
# ══════════════════════════════════════════════════════════════
def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,         QColor(28,28,35))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220,220,220))
    p.setColor(QPalette.ColorRole.Base,            QColor(20,20,28))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(36,36,46))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(48,48,62))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(220,220,220))
    p.setColor(QPalette.ColorRole.Text,            QColor(220,220,220))
    p.setColor(QPalette.ColorRole.Button,          QColor(42,42,54))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220,220,220))
    p.setColor(QPalette.ColorRole.BrightText,      Qt.GlobalColor.red)
    p.setColor(QPalette.ColorRole.Link,            QColor(0,180,220))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0,140,200))
    p.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    app.setPalette(p)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Multi-Radar Analyzer")
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
