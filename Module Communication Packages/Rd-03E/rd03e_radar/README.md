# RD-03E Radar Analyzer — Qt6 Desktop Application

A live graphing, test-save/open desktop tool for the **Ai-Thinker RD-03E 24 GHz mmWave radar** sensor.  
Built with **Qt6** (Widgets + Charts + SerialPort).

---

## Features

| Feature | Details |
|---|---|
| **COM port browser** | Lists all available serial ports with descriptions; refresh button |
| **Baud-rate selection** | Defaults to 256 000 baud (RD-03E native); supports 9600–256 000 |
| **Live chart** | Scrolling distance-vs-time line graph; configurable time window & Y-axis |
| **Live readout** | Colour-coded presence indicator (None / Static / Moving), distance in metres, frame counter |
| **Data table** | Timestamped rows, auto-scrolling, sortable |
| **Save test** | Exports all recorded frames to CSV |
| **Open & view test** | Loads a CSV and re-draws chart + table for offline review |
| **Dark theme** | Fusion dark palette; QChart dark theme |

---

## RD-03E Serial Protocol

The sensor transmits 7-byte frames at 256 000 baud, 8-N-1:

```
AA  AA  [status]  [distLow]  [distHigh]  55  55
```

| Byte | Meaning |
|---|---|
| `AA AA` | Frame header |
| `status` | `0` = no presence · `1` = static · `2` = moving |
| `distLow/High` | Distance in **centimetres**, uint16 little-endian |
| `55 55` | Frame footer |

`distance_m = ((distHigh << 8) | distLow) / 100.0`

---

## Build Requirements

- **Qt 6.2+** with modules: `Qt6::Widgets`, `Qt6::Charts`, `Qt6::SerialPort`
- C++17 compiler (MSVC 2019+, GCC 10+, Clang 12+)
- CMake 3.16+ **or** qmake

---

## Build Instructions

### Option A — CMake (recommended)

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release
```

### Option B — qmake

```bash
qmake rd03e_radar.pro
make          # Linux/macOS
nmake         # Windows MSVC
```

### Windows — Qt Online Installer

1. Install **Qt 6.x** for Desktop via the [Qt Online Installer](https://www.qt.io/download).
2. Make sure to select: **Qt Charts** and **Qt Serial Port** in the component list.
3. Open `rd03e_radar.pro` in **Qt Creator** and click **Build → Run**.

---

## Hardware Connection

The RD-03E connects via its UART pins. Use a USB-to-UART adapter (CH340, CP2102, FT232, etc.):

```
RD-03E  →  USB-UART adapter
  VCC   →  5 V
  GND   →  GND
  TX    →  RX  (adapter)
  RX    →  TX  (adapter)
```

Select the resulting COM port in the app and click **Connect**.

---

## CSV Format

```
timestamp_ms,time_s,distance_m,status_code,status_text
1711000000000,0.0000,1.230,2,Moving
1711000000050,0.0500,1.245,2,Moving
...
```

---

## Project Files

```
rd03e_radar/
├── CMakeLists.txt       — CMake build
├── rd03e_radar.pro      — qmake build
├── main.cpp             — Application entry point + dark palette
├── mainwindow.h/.cpp    — Main UI: chart, table, controls
├── rd03e_driver.h/.cpp  — Serial driver, frame parser
└── README.md
```

---

## License

MIT — see source headers.  
Protocol reverse-engineered from the [madhukartemba/RD-03E](https://github.com/madhukartemba/RD-03E) Arduino library (MIT).
