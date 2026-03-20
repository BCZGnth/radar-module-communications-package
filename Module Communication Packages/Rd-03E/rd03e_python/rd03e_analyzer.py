"""
RD-03E Radar Analyzer — Python / PyQt6 + pyqtgraph
====================================================
Requirements:
    pip install PyQt6 pyqtgraph pyserial

RD-03E frame protocol (7 bytes, 256000 baud 8-N-1):
    AA  AA  [status]  [distLow]  [distHigh]  55  55
    status: 0=none  1=static  2=moving
    distance = ((distHigh<<8)|distLow) / 100.0  →  metres
"""

import sys
import csv
import time
import struct
from datetime import datetime
from collections import deque

import serial
import serial.tools.list_ports

from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QObject, QTimer,
                           QDateTime, QMutex, QMutexLocker)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QComboBox, QPushButton, QSpinBox,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QSplitter, QFileDialog, QMessageBox, QToolBar,
    QStatusBar, QSizePolicy, QFrame
)
from PyQt6.QtGui import QAction, QIcon, QFont, QPalette, QColor, QPixmap

import pyqtgraph as pg


# ──────────────────────────────────────────────────────────────
#  Serial reader thread
# ──────────────────────────────────────────────────────────────
class SerialReader(QThread):
    frame_received = pyqtSignal(float, int, float)   # distance_m, status, elapsed_s
    error_occurred = pyqtSignal(str)

    BAUD_DEFAULT = 256000
    FRAME_LEN    = 7
    HEADER       = bytes([0xAA, 0xAA])
    FOOTER       = bytes([0x55, 0x55])

    def __init__(self, port: str, baud: int, parent=None):
        super().__init__(parent)
        self._port    = port
        self._baud    = baud
        self._running = False
        self._start_time: float | None = None
        self._mutex   = QMutex()

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False

    def run(self):
        self._running = True
        buf = bytearray()
        try:
            with serial.Serial(self._port, self._baud,
                               bytesize=8, parity='N', stopbits=1,
                               timeout=0.1) as ser:
                self._start_time = time.monotonic()
                while True:
                    with QMutexLocker(self._mutex):
                        if not self._running:
                            break
                    data = ser.read(64)
                    if data:
                        buf.extend(data)
                        buf = self._parse(buf)
        except serial.SerialException as e:
            self.error_occurred.emit(str(e))

    def _parse(self, buf: bytearray) -> bytearray:
        while len(buf) >= self.FRAME_LEN:
            # Search for AA AA ... 55 55
            idx = -1
            for i in range(len(buf) - self.FRAME_LEN + 1):
                if (buf[i] == 0xAA and buf[i+1] == 0xAA and
                        buf[i+5] == 0x55 and buf[i+6] == 0x55):
                    idx = i
                    break
            if idx < 0:
                # No frame — keep last 6 bytes
                return buf[-(self.FRAME_LEN - 1):]
            # Discard junk before frame
            buf = buf[idx:]
            status   = buf[2]
            raw_dist = buf[3] | (buf[4] << 8)
            dist_m   = raw_dist / 100.0
            elapsed  = time.monotonic() - self._start_time
            self.frame_received.emit(dist_m, status, elapsed)
            buf = buf[self.FRAME_LEN:]
        return buf


# ──────────────────────────────────────────────────────────────
#  Main window
# ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    STATUS_LABELS = {0: "None", 1: "Static", 2: "Moving"}
    STATUS_COLORS = {0: "#2ecc71", 1: "#f39c12", 2: "#e74c3c"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RD-03E Radar Analyzer")
        self.setMinimumSize(1100, 740)

        self._reader: SerialReader | None = None
        self._data: list[tuple[float, float, int]] = []   # (elapsed_s, dist_m, status)
        self._frame_count = 0

        # Rolling graph buffers
        self._times  = deque(maxlen=10000)
        self._dists  = deque(maxlen=10000)

        self._build_ui()
        self._build_toolbar()
        self._refresh_ports()
        self._set_connected(False)

    # ── UI construction ──────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Top control row ──
        top_row = QHBoxLayout()

        # Port group
        pg_box = QGroupBox("Serial Port")
        pg_lay = QHBoxLayout(pg_box)
        self.port_combo  = QComboBox(); self.port_combo.setMinimumWidth(180)
        self.baud_combo  = QComboBox()
        self.baud_combo.addItems(["256000","115200","57600","38400","19200","9600"])
        self.baud_combo.setCurrentText("256000")
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setFixedWidth(32)
        self.refresh_btn.setToolTip("Refresh port list")
        self.connect_btn    = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.connect_btn.setStyleSheet(
            "QPushButton{background:#27ae60;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 14px;}"
            "QPushButton:hover{background:#2ecc71;}")
        self.disconnect_btn.setStyleSheet(
            "QPushButton{background:#c0392b;color:white;font-weight:bold;"
            "border-radius:4px;padding:4px 14px;}"
            "QPushButton:hover{background:#e74c3c;}")
        for w in (QLabel("Port:"), self.port_combo, self.refresh_btn,
                  QLabel("Baud:"), self.baud_combo,
                  self.connect_btn, self.disconnect_btn):
            pg_lay.addWidget(w)

        # Settings group
        st_box = QGroupBox("Chart Settings")
        st_lay = QHBoxLayout(st_box)
        self.window_spin = QSpinBox()
        self.window_spin.setRange(5, 600); self.window_spin.setValue(30)
        self.window_spin.setSuffix(" s")
        self.maxdist_spin = QDoubleSpinBox()
        self.maxdist_spin.setRange(0.1, 10.0); self.maxdist_spin.setValue(6.0)
        self.maxdist_spin.setSingleStep(0.5); self.maxdist_spin.setSuffix(" m")
        apply_btn = QPushButton("Apply")
        for w in (QLabel("Window:"), self.window_spin,
                  QLabel("Max Dist:"), self.maxdist_spin, apply_btn):
            st_lay.addWidget(w)

        # Live readout group
        ro_box = QGroupBox("Live Readout")
        ro_lay = QHBoxLayout(ro_box)
        self.status_light  = QLabel("●")
        self.status_light.setFixedWidth(22)
        self.dist_label    = QLabel("-- m")
        self.presence_lbl  = QLabel("None")
        self.frame_lbl     = QLabel("Frames: 0")
        big = QFont(); big.setPointSize(14); big.setBold(True)
        self.dist_label.setFont(big)
        for w in (self.status_light, QLabel("Dist:"), self.dist_label,
                  QLabel("Status:"), self.presence_lbl, self.frame_lbl):
            ro_lay.addWidget(w)

        top_row.addWidget(pg_box, 3)
        top_row.addWidget(st_box, 2)
        top_row.addWidget(ro_box, 2)
        layout.addLayout(top_row)

        # ── Main splitter ──
        vsplit = QSplitter(Qt.Orientation.Vertical)

        # Chart
        self._build_chart()
        vsplit.addWidget(self.chart_widget)

        # Bottom: table + log
        hsplit = QSplitter(Qt.Orientation.Horizontal)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Timestamp", "Time (s)", "Distance (m)", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(160)
        self.log_view.setFont(QFont("Courier New", 9))
        self.log_view.setPlaceholderText("Event log...")

        hsplit.addWidget(self.table)
        hsplit.addWidget(self.log_view)
        hsplit.setSizes([650, 350])

        vsplit.addWidget(hsplit)
        vsplit.setSizes([430, 200])
        layout.addWidget(vsplit, 1)

        # ── Connections ──
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.refresh_btn.clicked.connect(self._refresh_ports)
        apply_btn.clicked.connect(self._apply_axes)

    def _build_chart(self):
        pg.setConfigOption('background', '#1a1a22')
        pg.setConfigOption('foreground', '#cccccc')

        self.chart_widget = pg.PlotWidget(title="RD-03E Distance Over Time")
        self.chart_widget.setLabel('left',   'Distance', units='m')
        self.chart_widget.setLabel('bottom', 'Time',     units='s')
        self.chart_widget.showGrid(x=True, y=True, alpha=0.25)
        self.chart_widget.setYRange(0, 6)
        self.chart_widget.setXRange(0, 30)
        self.chart_widget.setMinimumHeight(280)
        self.chart_widget.addLegend()

        pen = pg.mkPen(color='#00d4ff', width=2)
        self.dist_curve = self.chart_widget.plot(
            [], [], pen=pen, name="Distance (m)")

        # Horizontal threshold line (moveable)
        self.thresh_line = pg.InfiniteLine(
            pos=1.0, angle=0, movable=True,
            pen=pg.mkPen(color='#e74c3c', width=1, style=Qt.PenStyle.DashLine),
            label='Threshold: {value:.2f} m',
            labelOpts={'color': '#e74c3c', 'position': 0.05})
        self.chart_widget.addItem(self.thresh_line)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)

        save_act  = QAction("💾  Save Test", self)
        open_act  = QAction("📂  Open Test", self)
        clear_act = QAction("🗑  Clear Data", self)

        save_act.setToolTip("Save all recorded frames to CSV")
        open_act.setToolTip("Load a previously saved CSV test file")
        clear_act.setToolTip("Clear all current data")

        tb.addAction(save_act)
        tb.addAction(open_act)
        tb.addSeparator()
        tb.addAction(clear_act)

        save_act.triggered.connect(self._save_test)
        open_act.triggered.connect(self._open_test)
        clear_act.triggered.connect(self._clear_data)

    # ── Helpers ──────────────────────────────────────────────
    def _set_connected(self, connected: bool):
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.port_combo.setEnabled(not connected)
        self.baud_combo.setEnabled(not connected)
        self.refresh_btn.setEnabled(not connected)
        msg = f"Connected to {self.port_combo.currentData()}" if connected else "Disconnected"
        self.statusBar().showMessage(msg)
        if not connected:
            self.status_light.setStyleSheet("QLabel{color:#555;font-size:18px;}")
            self.presence_lbl.setText("None")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_view.append(f"[{ts}] {msg}")

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        if not ports:
            self.port_combo.addItem("(none)", "(none)")
        else:
            for p in ports:
                desc = p.description or "Serial Port"
                self.port_combo.addItem(f"{p.device} — {desc}", p.device)
        self._log(f"Found {len(ports)} serial port(s)")

    def _apply_axes(self):
        window   = self.window_spin.value()
        max_dist = self.maxdist_spin.value()
        if self._times:
            t_last = self._times[-1]
            x_min  = max(0.0, t_last - window)
            self.chart_widget.setXRange(x_min, x_min + window, padding=0)
        else:
            self.chart_widget.setXRange(0, window, padding=0)
        self.chart_widget.setYRange(0, max_dist, padding=0)

    # ── Slots ────────────────────────────────────────────────
    def _on_connect(self):
        port = self.port_combo.currentData()
        if not port or port == "(none)":
            QMessageBox.warning(self, "No Port", "Please select a valid COM port.")
            return
        baud = int(self.baud_combo.currentText())
        self._reader = SerialReader(port, baud, self)
        self._reader.frame_received.connect(self._on_frame)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.start()
        self._set_connected(True)
        self._log(f"Opened {port} @ {baud} baud")

    def _on_disconnect(self):
        if self._reader:
            self._reader.stop()
            self._reader.wait(2000)
            self._reader = None
        self._set_connected(False)
        self._log("Disconnected")

    def _on_frame(self, dist_m: float, status: int, elapsed: float):
        self._frame_count += 1
        self._data.append((elapsed, dist_m, status))
        self._times.append(elapsed)
        self._dists.append(dist_m)

        # Update chart
        window = self.window_spin.value()
        x_min  = max(0.0, elapsed - window)
        self.dist_curve.setData(list(self._times), list(self._dists))
        self.chart_widget.setXRange(x_min, x_min + window, padding=0)
        self.chart_widget.setYRange(0, self.maxdist_spin.value(), padding=0)

        # Live readout
        color = self.STATUS_COLORS.get(status, "#555")
        self.status_light.setStyleSheet(
            f"QLabel{{color:{color};font-size:20px;}}")
        self.dist_label.setText(f"{dist_m:.2f} m")
        self.presence_lbl.setText(self.STATUS_LABELS.get(status, "?"))
        self.frame_lbl.setText(f"Frames: {self._frame_count}")

        # Table (prepend)
        self.table.setSortingEnabled(False)
        self.table.insertRow(0)
        ts_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.table.setItem(0, 0, QTableWidgetItem(ts_str))
        self.table.setItem(0, 1, QTableWidgetItem(f"{elapsed:.3f}"))
        self.table.setItem(0, 2, QTableWidgetItem(f"{dist_m:.2f}"))
        self.table.setItem(0, 3, QTableWidgetItem(
            self.STATUS_LABELS.get(status, "?")))
        self.table.setSortingEnabled(True)
        if self.table.rowCount() > 2000:
            self.table.setRowCount(2000)

    def _on_error(self, msg: str):
        self._log(f"ERROR: {msg}")
        self._set_connected(False)
        QMessageBox.critical(self, "Serial Error", msg)

    # ── Save / Open ──────────────────────────────────────────
    def _save_test(self):
        if not self._data:
            QMessageBox.information(self, "Save Test", "No data to save.")
            return
        default = f"rd03e_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Test Data", default,
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["time_s", "distance_m", "status_code", "status_text"])
                for (t, d, s) in self._data:
                    w.writerow([f"{t:.4f}", f"{d:.3f}", s,
                                 self.STATUS_LABELS.get(s, "?")])
            self._log(f"Saved {len(self._data)} rows → {path}")
            QMessageBox.information(self, "Save Test",
                f"Saved {len(self._data)} frames to:\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))

    def _open_test(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Test Data", "",
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            rows = []
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        t = float(row["time_s"])
                        d = float(row["distance_m"])
                        s = int(row["status_code"])
                        rows.append((t, d, s))
                    except (KeyError, ValueError):
                        continue
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        # Clear and rebuild
        self._clear_data(confirm=False)
        self._data = rows
        self._times.extend(t for t, _, _ in rows)
        self._dists.extend(d for _, d, _ in rows)

        self.dist_curve.setData(list(self._times), list(self._dists))

        # Rebuild table
        self.table.setSortingEnabled(False)
        for (t, d, s) in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem("(saved)"))
            self.table.setItem(r, 1, QTableWidgetItem(f"{t:.3f}"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{d:.2f}"))
            self.table.setItem(r, 3, QTableWidgetItem(
                self.STATUS_LABELS.get(s, "?")))
        self.table.setSortingEnabled(True)

        # Fit axes
        if rows:
            t_max = max(t for t, _, _ in rows)
            d_max = max(d for _, d, _ in rows)
            self.chart_widget.setXRange(0, t_max if t_max > 0 else 30, padding=0.02)
            self.chart_widget.setYRange(0, max(self.maxdist_spin.value(), d_max * 1.1), padding=0.02)

        self._frame_count = len(rows)
        self.frame_lbl.setText(f"Frames: {self._frame_count}")
        import os
        self.statusBar().showMessage(
            f"Viewing: {os.path.basename(path)} ({len(rows)} frames)")
        self._log(f"Loaded {len(rows)} rows from {path}")
        QMessageBox.information(self, "Open Test",
            f"Loaded {len(rows)} frames from:\n{path}")

    def _clear_data(self, confirm=True):
        if confirm:
            if QMessageBox.question(self, "Clear Data",
                    "Clear all recorded data?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    ) != QMessageBox.StandardButton.Yes:
                return
        self._data.clear()
        self._times.clear()
        self._dists.clear()
        self._frame_count = 0
        self.dist_curve.setData([], [])
        self.table.setRowCount(0)
        self.dist_label.setText("-- m")
        self.frame_lbl.setText("Frames: 0")
        self._log("Data cleared")

    def closeEvent(self, event):
        if self._reader:
            self._reader.stop()
            self._reader.wait(2000)
        event.accept()


# ──────────────────────────────────────────────────────────────
#  Dark palette + entry point
# ──────────────────────────────────────────────────────────────
def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(30, 30, 36))
    p.setColor(QPalette.ColorRole.WindowText,       QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,             QColor(22, 22, 28))
    p.setColor(QPalette.ColorRole.AlternateBase,    QColor(38, 38, 46))
    p.setColor(QPalette.ColorRole.ToolTipBase,      QColor(50, 50, 62))
    p.setColor(QPalette.ColorRole.ToolTipText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Text,             QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,           QColor(45, 45, 56))
    p.setColor(QPalette.ColorRole.ButtonText,       QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.BrightText,       Qt.GlobalColor.red)
    p.setColor(QPalette.ColorRole.Link,             QColor(0, 180, 220))
    p.setColor(QPalette.ColorRole.Highlight,        QColor(0, 140, 200))
    p.setColor(QPalette.ColorRole.HighlightedText,  Qt.GlobalColor.white)
    app.setPalette(p)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("RD-03E Radar Analyzer")
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
