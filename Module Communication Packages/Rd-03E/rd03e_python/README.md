# RD-03E Radar Analyzer — Python Edition

Live graphing desktop app for the **Ai-Thinker RD-03E 24 GHz mmWave radar** sensor.  
Built with **PyQt6** + **pyqtgraph** + **pyserial**. Single-file, no build step required.

---

## Quick Start

```bash
pip install PyQt6 pyqtgraph pyserial
python rd03e_analyzer.py
```

---

## Features

| Feature | Details |
|---|---|
| **COM port browser** | Lists all available serial ports with descriptions; ⟳ refresh button |
| **Baud-rate selection** | Defaults to 256 000 baud (RD-03E native) |
| **Live scrolling chart** | pyqtgraph line plot; configurable time window & max-distance Y-axis |
| **Draggable threshold line** | Red dashed horizontal line — drag to set a distance threshold |
| **Live readout** | Colour-coded presence dot, distance in metres, frame counter |
| **Sortable data table** | One row per frame: timestamp, elapsed time, distance, status |
| **Save test** | Exports frames to CSV via file dialog |
| **Open & view test** | Loads CSV, rebuilds chart + table for offline review |
| **Dark theme** | Fusion dark palette + pyqtgraph dark background |

---

## RD-03E Protocol

7-byte frame at 256 000 baud 8-N-1:

```
AA  AA  [status]  [distLow]  [distHigh]  55  55
```

| Byte | Meaning |
|---|---|
| `AA AA` | Frame header |
| `status` | `0` = none · `1` = static · `2` = moving |
| `distLow/High` | Distance in cm, uint16 little-endian |
| `55 55` | Frame footer |

`distance_m = ((distHigh << 8) | distLow) / 100.0`

---

## CSV Format

```
time_s,distance_m,status_code,status_text
0.0000,1.230,2,Moving
0.0500,1.245,2,Moving
```

---

## Hardware Wiring

Use any USB-to-UART adapter (CH340, CP2102, FT232):

```
RD-03E  →  USB-UART
  VCC   →  5 V
  GND   →  GND
  TX    →  RX
  RX    →  TX
```

Select the resulting COM port in the app and click **Connect**.

---

## Dependencies

| Package | Purpose |
|---|---|
| `PyQt6` | GUI framework (widgets, threading, file dialogs) |
| `pyqtgraph` | Fast real-time chart |
| `pyserial` | Serial port access + port enumeration |

---

## Architecture

- **`SerialReader(QThread)`** — runs in a background thread; reads raw bytes,  
  parses 7-byte RD-03E frames, emits `frame_received(dist_m, status, elapsed_s)`.
- **`MainWindow(QMainWindow)`** — all UI; updates chart + table on each signal.
- Serial reading is fully non-blocking — the GUI never freezes.
